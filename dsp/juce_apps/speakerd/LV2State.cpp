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

    juce::Result decodeJuceBase64 (const juce::String& text, size_t maxBytes, juce::MemoryBlock& out)
    {
        static constexpr const char* alphabet = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+";
        out.reset();

        const auto dot = text.indexOfChar ('.');
        const auto prefix = text.substring (0, juce::jmax (0, dot));
        const auto payload = text.substring (dot + 1);

        if (dot <= 0 || prefix.length() > 10 || ! prefix.containsOnly ("0123456789"))
            return juce::Result::fail ("not JUCE base64: it starts with \"<size>.\", digits only");

        const auto size = (juce::uint64) prefix.getLargeIntValue();

        if (size > maxBytes)
            return juce::Result::fail ("not taken: " + juce::String ((juce::int64) size) + " bytes, more than "
                                       + juce::String ((juce::int64) maxBytes));

        if ((juce::uint64) payload.length() != (size * 8 + 5) / 6)
            return juce::Result::fail ("not JUCE base64: " + juce::String (payload.length()) + " characters for "
                                       + juce::String ((juce::int64) size) + " bytes");

        if (! payload.containsOnly (alphabet))
            return juce::Result::fail ("not JUCE base64: a character outside its alphabet");

        if (! out.fromBase64Encoding (text) || out.getSize() != size || out.toBase64Encoding() != text)
        {
            out.reset();
            return juce::Result::fail ("not JUCE base64: it does not read back as itself");
        }

        return juce::Result::ok();
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

        if (auto r = decodeJuceBase64 (lv2State.substring (range->getStart(), range->getEnd()), maxStateBytes, pluginState); r.failed())
            return juce::Result::fail ("the StateString of " + pluginUri + ": " + r.getErrorMessage());

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

        // The plugin's own state before (S0) and after (S1) the restore. A plugin ignores a
        // state it cannot read (JUCE plugins do, silently), and the LV2 wrapper still reports
        // success, so the only evidence is the state itself.
        juce::MemoryBlock before;
        if (auto r = unwrap (current.toString(), uri, before); r.failed())
            return r;

        instance.setStateInformation (wrapped.toRawUTF8(), (int) wrapped.getNumBytesAsUTF8());

        juce::MemoryBlock after;
        if (auto r = extract (instance, after); r.failed())
            return r;

        if (after.isEmpty() && ! pluginState.isEmpty())
            return juce::Result::fail ("the plugin did not take the state");

        // A state other than the current one that leaves the current one unchanged was not taken.
        // The limit: a state that restates the current values in other bytes (another attribute
        // order, say) is reported as not taken too. That fails the layer, which is the safe side.
        if (pluginState != before && after == before)
            return juce::Result::fail ("the plugin did not take the state: its state did not change");

        return juce::Result::ok();
    }

    juce::Result extract (juce::AudioPluginInstance& instance, juce::MemoryBlock& pluginState)
    {
        juce::MemoryBlock current;
        instance.getStateInformation (current);
        return unwrap (current.toString(), instance.getPluginDescription().fileOrIdentifier, pluginState);
    }
}
