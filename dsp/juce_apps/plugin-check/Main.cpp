// plugin-check: loads the complete, control and headless builds of one plugin through JUCE's
// own plugin hosting and checks that they fit together the way the remote plugin host needs:
//
//   1. all three builds load;
//   2. they expose the same parameters (IDs, names, defaults, value texts);
//   3. the headless build has no editor, the other two do;
//   4. state saved by the control build restores into the headless build, and back;
//   5. the headless build processes audio exactly like the complete build, and the control
//      build leaves audio untouched;
//   6. optionally, the control build's editor opens in a window (for a screenshot).
//
// Usage:
//   plugin-check --complete <bundle.lv2> --control <bundle.lv2> --headless <bundle.lv2>
//                [--probe <paramID>=<normalised value> ...] [--show-editor <seconds>]
//
// The probes are set before the audio check, so it can turn on something audible (a gain,
// a forced setting). Exit status 0 means every check passed.

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_gui_basics/juce_gui_basics.h>

#include <cmath>
#include <map>

namespace
{
    int failures = 0;

    void report (bool ok, const juce::String& what, const juce::String& detail = {})
    {
        std::cout << (ok ? "PASS  " : "FAIL  ") << what;
        if (detail.isNotEmpty())
            std::cout << "  (" << detail << ")";
        std::cout << std::endl;

        if (! ok)
            ++failures;
    }

    /** The parameter ID without the host-specific prefix. LV2 hosting reports a parameter as a
        URI under the plugin's own URI, which differs between the three builds; the part after
        the last '#', '/' or ':' is the ID the plugin declared. */
    juce::String bareId (const juce::String& id)
    {
        const auto cut = juce::jmax (id.lastIndexOfChar ('#'), id.lastIndexOfChar ('/'), id.lastIndexOfChar (':'));
        return cut >= 0 ? id.substring (cut + 1) : id;
    }

    struct ParamInfo
    {
        juce::String name;
        float defaultValue = 0.0f;
        juce::String textAt0, textAtHalf, textAt1;
        juce::AudioProcessorParameter* param = nullptr;
    };

    std::map<juce::String, ParamInfo> describe (juce::AudioPluginInstance& p)
    {
        std::map<juce::String, ParamInfo> out;

        for (auto* param : p.getParameters())
        {
            auto* hosted = dynamic_cast<juce::HostedAudioProcessorParameter*> (param);
            const auto id = bareId (hosted != nullptr ? hosted->getParameterID()
                                                      : juce::String (param->getParameterIndex()));
            out[id] = { param->getName (256), param->getDefaultValue(),
                        param->getText (0.0f, 64), param->getText (0.5f, 64), param->getText (1.0f, 64),
                        param };
        }

        return out;
    }

    std::unique_ptr<juce::AudioPluginInstance> load (juce::AudioPluginFormatManager& formats,
                                                     const juce::String& bundle,
                                                     double sampleRate, int blockSize)
    {
        juce::OwnedArray<juce::PluginDescription> found;

        for (auto* format : formats.getFormats())
            if (format->getName() == "LV2")
                format->findAllTypesForFile (found, juce::File (bundle).getFullPathName());

        if (found.isEmpty())
        {
            report (false, "load " + bundle, "no LV2 plugin found in the bundle");
            return {};
        }

        juce::String error;
        auto instance = formats.createPluginInstance (*found.getFirst(), sampleRate, blockSize, error);
        report (instance != nullptr, "load " + found.getFirst()->name, error);
        return instance;
    }

    /** Runs a 100 Hz sine at -20 dBFS through the plugin and returns the output. */
    juce::AudioBuffer<float> render (juce::AudioPluginInstance& p, double sampleRate, int blockSize,
                                     double seconds, juce::AudioBuffer<float>& inputOut)
    {
        const int channels = juce::jmax (p.getTotalNumInputChannels(), p.getTotalNumOutputChannels());
        const int total = (int) (seconds * sampleRate);

        juce::AudioBuffer<float> input (channels, total), output (channels, total);
        input.clear();

        for (int i = 0; i < total; ++i)
            input.setSample (0, i, 0.1f * (float) std::sin (2.0 * juce::MathConstants<double>::pi * 100.0 * i / sampleRate));

        p.prepareToPlay (sampleRate, blockSize);

        juce::AudioBuffer<float> block (channels, blockSize);
        juce::MidiBuffer midi;

        for (int pos = 0; pos < total; pos += blockSize)
        {
            const int n = juce::jmin (blockSize, total - pos);
            block.setSize (channels, n, false, false, true);

            for (int c = 0; c < channels; ++c)
                block.copyFrom (c, 0, input, c, pos, n);

            p.processBlock (block, midi);

            for (int c = 0; c < channels; ++c)
                output.copyFrom (c, pos, block, c, 0, n);
        }

        p.releaseResources();
        inputOut = input;
        return output;
    }

    double rms (const juce::AudioBuffer<float>& b, int start)
    {
        return b.getRMSLevel (0, start, b.getNumSamples() - start);
    }

    double maxDifference (const juce::AudioBuffer<float>& a, const juce::AudioBuffer<float>& b)
    {
        double worst = 0.0;
        for (int i = 0; i < juce::jmin (a.getNumSamples(), b.getNumSamples()); ++i)
            worst = juce::jmax (worst, (double) std::abs (a.getSample (0, i) - b.getSample (0, i)));
        return worst;
    }

    void setProbes (juce::AudioPluginInstance& p, const juce::StringArray& probes)
    {
        auto params = describe (p);

        for (const auto& probe : probes)
        {
            const auto id = probe.upToFirstOccurrenceOf ("=", false, false);
            const auto value = probe.fromFirstOccurrenceOf ("=", false, false).getFloatValue();

            if (auto it = params.find (id); it != params.end())
                it->second.param->setValueNotifyingHost (value);
            else
                report (false, "probe " + id, "no such parameter");
        }
    }

    /** Processes one block of silence. A hosted LV2 plugin receives parameter changes from its
        host only while it processes (they travel on its atom port), and reports its values
        back the same way, so the host and the plugin agree only after a block has run. */
    void pump (juce::AudioPluginInstance& p, double sampleRate, int blockSize)
    {
        const int channels = juce::jmax (1, p.getTotalNumInputChannels(), p.getTotalNumOutputChannels());
        juce::AudioBuffer<float> block (channels, blockSize);
        juce::MidiBuffer midi;

        p.prepareToPlay (sampleRate, blockSize);
        for (int i = 0; i < 4; ++i)
        {
            block.clear();
            p.processBlock (block, midi);
        }
        p.releaseResources();
    }

    /** Saves state from one instance and restores it into the other.

        JUCE saves an LV2 plugin's state as a preset stamped with the plugin's URI, both as the
        preset's subject and as the key of the state itself. The control and headless builds
        have different URIs, so a saved state is retargeted -- every occurrence of the source
        plugin's URI replaced by the destination's -- before it is restored. The controller and
        speakerd have to do the same whenever state crosses between the two builds. */
    void transferState (juce::AudioPluginInstance& from, juce::AudioPluginInstance& to,
                        double sampleRate, int blockSize)
    {
        pump (from, sampleRate, blockSize);

        juce::MemoryBlock state;
        from.getStateInformation (state);

        const auto fromUri = from.getPluginDescription().fileOrIdentifier;
        const auto toUri   = to.getPluginDescription().fileOrIdentifier;
        const auto retargeted = state.toString().replace (fromUri, toUri);

        to.setStateInformation (retargeted.toRawUTF8(), (int) retargeted.getNumBytesAsUTF8());

        pump (to, sampleRate, blockSize);
    }

    /** Shows the editor in a window and keeps it up for `seconds`. */
    void showEditor (juce::AudioPluginInstance& p, int seconds)
    {
        std::unique_ptr<juce::AudioProcessorEditor> editor (p.createEditorIfNeeded());
        report (editor != nullptr, "control editor opens");

        if (editor == nullptr)
            return;

        juce::DocumentWindow window (p.getName(), juce::Colours::black, 0);
        window.setUsingNativeTitleBar (false);
        window.setTitleBarHeight (0);
        window.setContentNonOwned (editor.get(), true);
        window.setTopLeftPosition (0, 0);
        window.setVisible (true);

        std::cout << "EDITOR " << editor->getWidth() << "x" << editor->getHeight() << std::endl;
        juce::MessageManager::getInstance()->runDispatchLoopUntil (seconds * 1000);

        window.clearContentComponent();
        editor.reset();
    }
}

int main (int argc, char* argv[])
{
    juce::ScopedJuceInitialiser_GUI juce;

    juce::String complete, control, headless;
    juce::StringArray probes;
    int editorSeconds = 0;

    for (int i = 1; i < argc; ++i)
    {
        const juce::String arg (argv[i]);
        const juce::String next = i + 1 < argc ? juce::String (argv[i + 1]) : juce::String();

        if      (arg == "--complete")    { complete = next; ++i; }
        else if (arg == "--control")     { control = next; ++i; }
        else if (arg == "--headless")    { headless = next; ++i; }
        else if (arg == "--probe")       { probes.add (next); ++i; }
        else if (arg == "--show-editor") { editorSeconds = next.getIntValue(); ++i; }
        else
        {
            std::cerr << "unknown argument " << arg << std::endl;
            return 2;
        }
    }

    if (complete.isEmpty() || control.isEmpty() || headless.isEmpty())
    {
        std::cerr << "usage: plugin-check --complete <bundle> --control <bundle> --headless <bundle>"
                     " [--probe id=value ...] [--show-editor seconds]" << std::endl;
        return 2;
    }

    constexpr double sampleRate = 48000.0;
    constexpr int blockSize = 512;

    juce::AudioPluginFormatManager formats;
    juce::addDefaultFormatsToManager (formats);

    // 1. Load
    auto pComplete = load (formats, complete, sampleRate, blockSize);
    auto pControl  = load (formats, control,  sampleRate, blockSize);
    auto pHeadless = load (formats, headless, sampleRate, blockSize);

    if (pComplete == nullptr || pControl == nullptr || pHeadless == nullptr)
        return 1;

    // 2. Same parameters
    {
        auto dComplete = describe (*pComplete), dControl = describe (*pControl), dHeadless = describe (*pHeadless);

        auto compare = [&] (std::map<juce::String, ParamInfo>& other, const juce::String& otherName)
        {
            juce::String problem;

            if (other.size() != dComplete.size())
                problem = juce::String ((int) dComplete.size()) + " parameters vs " + juce::String ((int) other.size());

            for (auto& [id, a] : dComplete)
            {
                if (problem.isNotEmpty())
                    break;

                auto it = other.find (id);
                if (it == other.end())
                    problem = id + " missing";
                else if (a.name != it->second.name)
                    problem = id + ": name '" + a.name + "' vs '" + it->second.name + "'";
                else if (std::abs (a.defaultValue - it->second.defaultValue) > 1.0e-6f)
                    problem = id + ": default differs";
                else if (a.textAt0 != it->second.textAt0 || a.textAtHalf != it->second.textAtHalf || a.textAt1 != it->second.textAt1)
                    problem = id + ": value text differs ('" + a.textAt1 + "' vs '" + it->second.textAt1 + "')";
            }

            report (problem.isEmpty(), "parameters of " + otherName + " match the complete build",
                    problem.isEmpty() ? juce::String ((int) dComplete.size()) + " parameters" : problem);
        };

        compare (dControl, "control");
        compare (dHeadless, "headless");
    }

    // 3. Editors
    report (pComplete->hasEditor(),   "complete build has an editor");
    report (pControl->hasEditor(),    "control build has an editor");
    report (! pHeadless->hasEditor(), "headless build has no editor");

    // 4. State, control -> headless and back. Compared as the saved state itself: the host's
    //    cached parameter values are not reliable here (it keeps the raw number it was given,
    //    while the plugin quantises), but a plugin that restored everything saves exactly what
    //    it was given.
    {
        auto stateOf = [&] (juce::AudioPluginInstance& p)
        {
            pump (p, sampleRate, blockSize);
            juce::MemoryBlock st;
            p.getStateInformation (st);
            return st.toString().replace (p.getPluginDescription().fileOrIdentifier, "<plugin>");
        };

        juce::Random random (1234);

        for (auto* param : pControl->getParameters())
            param->setValueNotifyingHost (random.nextFloat());

        transferState (*pControl, *pHeadless, sampleRate, blockSize);
        report (stateOf (*pControl) == stateOf (*pHeadless), "state saved by control restores into headless");

        for (auto* param : pHeadless->getParameters())
            param->setValueNotifyingHost (random.nextFloat());

        transferState (*pHeadless, *pControl, sampleRate, blockSize);
        report (stateOf (*pHeadless) == stateOf (*pControl), "state saved by headless restores into control");
    }

    // 5. Audio. The complete build gets the probes directly. The headless build gets them only
    //    the way a speaker would: set in the control build, saved, and restored as state.
    {
        for (auto* p : { pComplete.get(), pControl.get(), pHeadless.get() })
        {
            for (auto* param : p->getParameters())
                param->setValueNotifyingHost (param->getDefaultValue());

            pump (*p, sampleRate, blockSize);
        }

        setProbes (*pComplete, probes);
        setProbes (*pControl, probes);
        transferState (*pControl, *pHeadless, sampleRate, blockSize);

        juce::AudioBuffer<float> in;
        const auto outComplete = render (*pComplete, sampleRate, blockSize, 2.0, in);
        const auto outHeadless = render (*pHeadless, sampleRate, blockSize, 2.0, in);
        const auto outControl  = render (*pControl,  sampleRate, blockSize, 2.0, in);

        const int settled = (int) sampleRate;    // measure over the second second
        const auto gainDb = juce::Decibels::gainToDecibels (rms (outComplete, settled) / rms (in, settled));

        report (true, "complete build gain at 100 Hz", juce::String (gainDb, 2) + " dB");

        const auto diff = maxDifference (outComplete, outHeadless);
        report (diff < 1.0e-6, "headless, set up from the control build's state, sounds like the complete build",
                "max difference " + juce::String (diff, 9));

        const auto passthrough = maxDifference (in, outControl);
        report (passthrough <= 0.0, "control build leaves audio untouched", "max difference " + juce::String (passthrough, 9));

        if (! probes.isEmpty())
            report (std::abs (gainDb) > 1.0, "probes change the complete build's output",
                    "otherwise the audio check proves nothing");
    }

    // 6. Editor
    if (editorSeconds > 0)
        showEditor (*pControl, editorSeconds);

    pComplete.reset();
    pControl.reset();
    pHeadless.reset();

    std::cout << (failures == 0 ? "ALL CHECKS PASSED" : juce::String (failures) + " CHECK(S) FAILED") << std::endl;
    return failures == 0 ? 0 : 1;
}
