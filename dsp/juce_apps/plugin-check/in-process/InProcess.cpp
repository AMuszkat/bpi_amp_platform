// in-process: one build of a plugin linked straight into this program, the way the controller
// app links its control builds, for what LV2 hosting cannot see: the plugin's own getters, how
// its monitors are declared, and its own saved state.
//
// It is compiled once per build (complete, control, headless) against that build's shared
// code; SPEAKER_COMPLETE, SPEAKER_CONTROL and SPEAKER_HEADLESS say which. The plugin's
// plugin-check.monitors.h names the getters that read its monitors (docs/protocol.md, "How a
// plugin publishes a monitor"). It checks:
//
//   1. the monitors are the parameters whose IDs start with "mon_", one per getter, declared
//      after every other parameter, not automatable, of category output meter;
//   2. the saved state carries no monitor, and restoring a state that names one sets nothing;
//   3. complete and headless: after a -20 dBFS tone, each monitor holds what its getter reads;
//      control: setting a monitor moves its getter.
//
// The complete build also prints "EXPECT <id>=<value>:<tolerance>" for each monitor after the
// tone, which run.sh hands to plugin-check: the hosted headless build must publish the same.
//
// Usage: in-process [--probe <paramID>=<normalised value> ...]

#include <juce_audio_processors/juce_audio_processors.h>

#include <cmath>
#include <iostream>
#include <limits>
#include <map>

#if __has_include("plugin-check.monitors.h")
 #include "plugin-check.monitors.h"
 #define PLUGIN_CHECK_HAS_GETTERS 1
#else
 #define PLUGIN_CHECK_HAS_GETTERS 0
#endif

namespace
{
   #if SPEAKER_CONTROL
    const char* const buildName = "control";
   #elif SPEAKER_HEADLESS
    const char* const buildName = "headless";
   #else
    const char* const buildName = "complete";
   #endif

    int failures = 0;

    void report (bool ok, const juce::String& what, const juce::String& detail = {})
    {
        std::cout << (ok ? "PASS  " : "FAIL  ") << "in-process " << buildName << ": " << what;
        if (detail.isNotEmpty())
            std::cout << "  (" << detail << ")";
        std::cout << std::endl;

        if (! ok)
            ++failures;
    }

    bool isMonitor (const juce::String& id) { return id.startsWith ("mon_"); }

    juce::String idOf (juce::AudioProcessorParameter& p)
    {
        if (auto* withId = dynamic_cast<juce::AudioProcessorParameterWithID*> (&p))
            return withId->paramID;

        return {};
    }

    /** The monitors by ID, in declaration order. */
    std::map<juce::String, juce::RangedAudioParameter*> monitorsOf (juce::AudioProcessor& p)
    {
        std::map<juce::String, juce::RangedAudioParameter*> out;

        for (auto* param : p.getParameters())
            if (isMonitor (idOf (*param)))
                out[idOf (*param)] = dynamic_cast<juce::RangedAudioParameter*> (param);

        return out;
    }

    float denormalised (juce::RangedAudioParameter& p) { return p.convertFrom0to1 (p.getValue()); }

    juce::String savedState (juce::AudioProcessor& p)
    {
        juce::MemoryBlock block;
        p.getStateInformation (block);

        if (auto xml = juce::AudioProcessor::getXmlFromBinary (block.getData(), (int) block.getSize()))
            return xml->toString();

        return {};
    }

    void setProbes (juce::AudioProcessor& p, const juce::StringArray& probes)
    {
        for (const auto& probe : probes)
        {
            const auto id = probe.upToFirstOccurrenceOf ("=", false, false);
            bool found = false;

            for (auto* param : p.getParameters())
            {
                if (idOf (*param) == id)
                {
                    param->setValueNotifyingHost (probe.fromFirstOccurrenceOf ("=", false, false).getFloatValue());
                    found = true;
                }
            }

            if (! found)
                report (false, "probe " + id, "no such parameter");
        }
    }

    /** The same tone as plugin-check's: a 100 Hz sine at -20 dBFS, 2 s at 48 kHz in 512-sample
        blocks. */
    void playTone (juce::AudioProcessor& p)
    {
        constexpr double sampleRate = 48000.0;
        constexpr int blockSize = 512;
        const int total = (int) (2.0 * sampleRate);
        const int channels = juce::jmax (1, p.getTotalNumInputChannels(), p.getTotalNumOutputChannels());

        p.setRateAndBufferSizeDetails (sampleRate, blockSize);
        p.prepareToPlay (sampleRate, blockSize);

        juce::AudioBuffer<float> block (channels, blockSize);
        juce::MidiBuffer midi;

        for (int pos = 0; pos < total; pos += blockSize)
        {
            const int n = juce::jmin (blockSize, total - pos);
            block.setSize (channels, n, false, false, true);
            block.clear();

            for (int i = 0; i < n; ++i)
                block.setSample (0, i, 0.1f * (float) std::sin (2.0 * juce::MathConstants<double>::pi * 100.0 * (pos + i) / sampleRate));

            p.processBlock (block, midi);
        }

        p.releaseResources();
    }
}

int main (int argc, char* argv[])
{
    juce::ScopedJuceInitialiser_GUI juce;

    juce::StringArray probes;

    for (int i = 1; i < argc; ++i)
    {
        const juce::String arg (argv[i]);

        if (arg == "--probe" && i + 1 < argc)
        {
            probes.add (argv[++i]);
        }
        else
        {
            std::cerr << "usage: in-process [--probe id=value ...]" << std::endl;
            return 2;
        }
    }

    std::unique_ptr<juce::AudioProcessor> processor (createPluginFilter());

    if (processor == nullptr)
    {
        report (false, "createPluginFilter() returns a processor");
        return 1;
    }

    auto& p = *processor;
    auto monitors = monitorsOf (p);

   #if PLUGIN_CHECK_HAS_GETTERS
    std::map<juce::String, const pluginCheck::MonitorGetter*> getters;

    for (const auto& g : pluginCheck::monitorGetters)
        getters[g.id] = &g;
   #else
    std::map<juce::String, const void*> getters;
   #endif

    // 1. Declarations
    {
        juce::StringArray ids, expected;

        for (const auto& m : monitors)
            ids.add (m.first);

        for (const auto& g : getters)
            expected.add (g.first);

        report (ids == expected, "the monitors are the ones plugin-check.monitors.h reads",
                "mon_ parameters: [" + ids.joinIntoString (", ") + "], getters: [" + expected.joinIntoString (", ") + "]");

        int lastOther = -1, firstMonitor = std::numeric_limits<int>::max();

        for (auto* param : p.getParameters())
        {
            if (isMonitor (idOf (*param)))
                firstMonitor = juce::jmin (firstMonitor, param->getParameterIndex());
            else
                lastOther = juce::jmax (lastOther, param->getParameterIndex());
        }

        juce::String problem;

        for (const auto& [id, param] : monitors)
        {
            if (param == nullptr)
                problem << id << " is not a ranged parameter; ";
            else if (param->isAutomatable())
                problem << id << " is automatable; ";
            else if (param->getCategory() != juce::AudioProcessorParameter::outputMeter)
                problem << id << " is not of category output meter; ";
        }

        if (! monitors.empty() && firstMonitor < lastOther)
            problem << "a monitor is declared before another parameter; ";

        report (problem.isEmpty(), "monitors are declared last, not automatable, of category output meter",
                problem.isEmpty() ? juce::String ((int) p.getParameters().size()) + " parameters, "
                                      + juce::String ((int) monitors.size()) + " of them monitors"
                                  : problem);
    }

    // 2. State: never saved, never restored.
    {
        const auto saved = savedState (p);
        report (saved.isNotEmpty() && ! saved.contains ("mon_"), "the saved state carries no monitor");

        // A state that names every monitor, with a value other than the one it holds.
        auto xml = juce::parseXML (saved);
        std::map<juce::String, float> before;

        for (auto& [id, param] : monitors)
        {
            param->setValue (0.25f);                    // known values, away from both ends
            before[id] = param->getValue();

            if (xml != nullptr)
            {
                auto* child = xml->createNewChildElement ("PARAM");
                child->setAttribute ("id", id);
                child->setAttribute ("value", (double) param->getNormalisableRange().end);
            }
        }

        if (xml != nullptr)
        {
            juce::MemoryBlock crafted;
            juce::AudioProcessor::copyXmlToBinary (*xml, crafted);
            p.setStateInformation (crafted.getData(), (int) crafted.getSize());
        }

        juce::String moved;

        for (auto& [id, param] : monitors)
            if (! juce::exactlyEqual (param->getValue(), before[id]))
                moved << id << " ";

        report (xml != nullptr && moved.isEmpty(), "restoring a state that names the monitors sets none of them",
                moved.isEmpty() ? juce::String() : "moved: " + moved);

        const auto again = savedState (p);
        report (again.isNotEmpty() && ! again.contains ("mon_"), "a restored state that named them does not carry them on");
    }

    // 3. The getters
   #if PLUGIN_CHECK_HAS_GETTERS
   #if SPEAKER_CONTROL
    juce::ignoreUnused (probes, &setProbes, &playTone, &denormalised);   // nothing plays here

    for (auto& [id, param] : monitors)
    {
        const auto* getter = getters[id];

        if (getter == nullptr)
            continue;                                   // reported by check 1

        const auto& range = param->getNormalisableRange();
        juce::String problem;

        for (const auto fraction : { 0.0f, 0.3f, 1.0f })
        {
            const auto value = param->convertFrom0to1 (fraction);
            param->setValueNotifyingHost (param->convertTo0to1 (value));
            const auto read = getter->read (p);

            if (std::abs (read - value) > juce::jmax (getter->tolerance, range.interval))
                problem << "set " << value << ", getter reads " << read << "; ";
        }

        report (problem.isEmpty(), "setting " + id + " moves the control build's getter", problem);
    }
   #else
    setProbes (p, probes);
    playTone (p);

    for (auto& [id, param] : monitors)
    {
        const auto* getter = getters[id];

        if (getter == nullptr)
            continue;                                   // reported by check 1

        const auto read = getter->read (p);
        const auto published = denormalised (*param);
        const auto tolerance = juce::jmax (getter->tolerance, param->getNormalisableRange().interval);

        report (std::abs (read - published) <= tolerance, id + " holds what its getter reads after a -20 dBFS tone",
                "getter " + juce::String (read, 3) + ", parameter " + juce::String (published, 3));

       #if SPEAKER_COMPLETE
        std::cout << "EXPECT " << id << "=" << read << ":" << getter->tolerance << std::endl;
       #endif
    }
   #endif
   #else
    juce::ignoreUnused (probes, &setProbes, &playTone, &denormalised);
    report (monitors.empty(), "no plugin-check.monitors.h, so no monitors");
   #endif

    processor.reset();

    std::cout << (failures == 0 ? "IN-PROCESS PASSED" : juce::String (failures) + " IN-PROCESS CHECK(S) FAILED") << std::endl;
    return failures == 0 ? 0 : 1;
}
