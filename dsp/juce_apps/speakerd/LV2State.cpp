#include "LV2State.h"

#include <optional>

namespace speakerd::lv2state
{
    namespace
    {
        /** Where the literal value of <uri:StateString> sits in a Turtle state: the range between
            its quotes. lilv writes it as "..." (or """...""" if it ever had to); JUCE base64 has
            no quote or backslash in it, so the content is the raw text. */
        std::optional<juce::Range<int>> findStateString (const juce::String& turtle, const juce::String& pluginUri)
        {
            const auto key = "<" + pluginUri + ":StateString>";
            const auto keyAt = turtle.indexOf (key);

            if (keyAt < 0)
                return std::nullopt;

            const auto open = turtle.indexOfChar (keyAt + key.length(), '"');

            if (open < 0)
                return std::nullopt;

            const bool triple = turtle.substring (open, open + 3) == "\"\"\"";
            const auto start = open + (triple ? 3 : 1);
            const auto end = turtle.indexOf (start, triple ? "\"\"\"" : "\"");

            if (end < 0)
                return std::nullopt;

            return juce::Range<int> (start, end);
        }
    }

    juce::Result wrap (const juce::String& lv2State, const juce::String& pluginUri,
                       const juce::MemoryBlock& pluginState, juce::String& wrapped)
    {
        const auto range = findStateString (lv2State, pluginUri);

        if (! range.has_value())
            return juce::Result::fail ("the plugin's LV2 state has no <" + pluginUri
                                       + ":StateString>, so it is not a JUCE plugin speakerd can give a state to");

        wrapped = lv2State.substring (0, range->getStart())
                + pluginState.toBase64Encoding()
                + lv2State.substring (range->getEnd());

        return juce::Result::ok();
    }

    juce::Result unwrap (const juce::String& lv2State, const juce::String& pluginUri,
                         juce::MemoryBlock& pluginState)
    {
        const auto range = findStateString (lv2State, pluginUri);

        if (! range.has_value())
            return juce::Result::fail ("no <" + pluginUri + ":StateString> in the plugin's LV2 state");

        pluginState.reset();

        if (! pluginState.fromBase64Encoding (lv2State.substring (range->getStart(), range->getEnd())))
            return juce::Result::fail ("the StateString of " + pluginUri + " is not JUCE base64");

        return juce::Result::ok();
    }

    juce::Result restore (juce::AudioPluginInstance& instance, const juce::MemoryBlock& pluginState)
    {
        const auto uri = instance.getPluginDescription().fileOrIdentifier;

        juce::MemoryBlock current;
        instance.getStateInformation (current);

        juce::String wrapped;
        if (auto r = wrap (current.toString(), uri, pluginState, wrapped); r.failed())
            return r;

        instance.setStateInformation (wrapped.toRawUTF8(), (int) wrapped.getNumBytesAsUTF8());

        // Read it back: a state the plugin did not take would otherwise be silently ignored.
        juce::MemoryBlock check;
        if (auto r = extract (instance, check); r.failed())
            return r;

        if (check.isEmpty() && ! pluginState.isEmpty())
            return juce::Result::fail ("the plugin did not take the state");

        return juce::Result::ok();
    }

    juce::Result extract (juce::AudioPluginInstance& instance, juce::MemoryBlock& pluginState)
    {
        juce::MemoryBlock current;
        instance.getStateInformation (current);
        return unwrap (current.toString(), instance.getPluginDescription().fileOrIdentifier, pluginState);
    }
}
