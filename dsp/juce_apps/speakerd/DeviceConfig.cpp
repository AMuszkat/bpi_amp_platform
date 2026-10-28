#include "DeviceConfig.h"

#include <cmath>
#include <cstdio>
#include <set>
#include <string>
#include <tuple>
#include <vector>

namespace speakerd
{
    juce::String toString (Role r)
    {
        switch (r)
        {
            case Role::left:  return "left";
            case Role::right: return "right";
            case Role::mono:  return "mono";
        }

        return "mono";
    }

    namespace
    {
        using juce::Result;
        using juce::String;

        /** The first key that an object of the JSON text has twice, as a path ("limits.tweeter"),
            or an empty string. JUCE's parser keeps the last of two equal keys, so
            "tweeter": "muted" followed by "tweeter": "on" would read as "on". The text has
            parsed already, so this only follows its structure. */
        String findRepeatedKey (const String& text)
        {
            const std::string s (text.toRawUTF8());

            struct Frame
            {
                bool object = false;
                bool expectKey = false;
                std::set<std::string> keys;
                std::string name;
            };

            std::vector<Frame> stack;
            std::string lastKey;

            // A string token from s[i] == '"': its decoded value; i ends on its closing quote.
            const auto readString = [&] (size_t& i)
            {
                std::string out;

                for (++i; i < s.size() && s[i] != '"'; ++i)
                {
                    if (s[i] != '\\' || i + 1 >= s.size())
                    {
                        out += s[i];
                        continue;
                    }

                    const auto c = s[++i];

                    if (c == 'u' && i + 4 < s.size())
                    {
                        const auto code = String (s.substr (i + 1, 4)).getHexValue32();
                        i += 4;
                        out += String::charToString ((juce::juce_wchar) code).toStdString();
                    }
                    else
                    {
                        out += c == 'n' ? '\n' : c == 't' ? '\t' : c == 'r' ? '\r' : c == 'b' ? '\b' : c == 'f' ? '\f' : c;
                    }
                }

                return out;
            };

            const auto path = [&] (const std::string& key)
            {
                std::string p;
                for (const auto& frame : stack)
                    if (! frame.name.empty())
                        p += frame.name + ".";
                return String (p + key);
            };

            for (size_t i = 0; i < s.size(); ++i)
            {
                const auto c = s[i];

                if (c == '"')
                {
                    auto value = readString (i);

                    if (! stack.empty() && stack.back().object && stack.back().expectKey)
                    {
                        if (! stack.back().keys.insert (value).second)
                            return path (value);

                        stack.back().expectKey = false;
                        lastKey = value;
                    }
                }
                else if (c == '{' || c == '[')
                {
                    Frame frame;
                    frame.object = c == '{';
                    frame.expectKey = frame.object;
                    frame.name = (! stack.empty() && stack.back().object) ? lastKey : std::string();
                    stack.push_back (frame);
                }
                else if (c == '}' || c == ']')
                {
                    if (! stack.empty())
                        stack.pop_back();
                }
                else if (c == ',' && ! stack.empty() && stack.back().object)
                {
                    stack.back().expectKey = true;
                }
            }

            return {};
        }

        /** Fails on a key of `object` that is not one of `known`. */
        Result onlyKnownKeys (const juce::var& object, std::initializer_list<const char*> known, const String& where)
        {
            juce::StringArray names;
            for (const auto* k : known)
                names.add (k);

            for (const auto& property : object.getDynamicObject()->getProperties())
                if (! names.contains (property.name.toString()))
                    return Result::fail (where + "unknown key '" + property.name.toString() + "' (this speakerd knows \""
                                         + names.joinIntoString ("\", \"") + "\"; a key it does not know could be a "
                                         "setting it would not apply)");

            return Result::ok();
        }

        Result readString (const juce::var& object, const char* key, String& out, bool required)
        {
            const auto& value = object[key];

            if (value.isVoid())
                return required ? Result::fail (String ("'") + key + "' is missing")
                                : Result::ok();

            if (! value.isString() || value.toString().isEmpty())
                return Result::fail (String ("'") + key + "' must be a non-empty string");

            out = value.toString();
            return Result::ok();
        }

        /** "limits": { "gain_db": <at most 0>, "tweeter": "muted" or "on" }, both keys required
            and no other. Without it, the limits are failSafeLimits. */
        Result parseLimits (const juce::var& json, DeviceConfig& out)
        {
            const String expected = "{ \"gain_db\": <dB, at most 0>, \"tweeter\": \"muted\" or \"on\" }";

            // Present is present, even as null: only a file without the key gets the fail-safe.
            if (! json.getDynamicObject()->hasProperty ("limits"))
            {
                out.limits = DeviceConfig::failSafeLimits;
                out.limitsFrom = DeviceConfig::LimitsFrom::notInFile;
                return Result::ok();
            }

            const auto& limits = json["limits"];

            if (limits.getDynamicObject() == nullptr)
                return Result::fail ("'limits' must be " + expected + " (no limit at all is { \"gain_db\": 0, \"tweeter\": \"on\" })");

            if (auto r = onlyKnownKeys (limits, { "gain_db", "tweeter" }, "limits: "); r.failed())
                return r;

            DeviceConfig::Limits result;
            const auto& gain = limits["gain_db"];

            if (! (gain.isInt() || gain.isInt64() || gain.isDouble()))
                return Result::fail ("limits: 'gain_db' " + String (gain.isVoid() ? "is missing" : "must be a number")
                                     + ": the output gain and ceiling in dB, at most 0");

            result.gainDb = (double) gain;

            if (! std::isfinite (result.gainDb) || result.gainDb > 0.0)
                return Result::fail ("limits: 'gain_db' must be a finite number of dB, at most 0, not " + gain.toString());

            if (std::fpclassify (result.gainDb) == FP_ZERO)
                result.gainDb = 0.0;   // not -0

            const auto& tweeter = limits["tweeter"];

            if (tweeter.isString() && tweeter.toString() == "muted")
                result.tweeterMuted = true;
            else if (tweeter.isString() && tweeter.toString() == "on")
                result.tweeterMuted = false;
            else
                return Result::fail ("limits: 'tweeter' must be \"muted\" or \"on\""
                                     + String (tweeter.isVoid() ? ", and is missing" : ", not " + juce::JSON::toString (tweeter)));

            out.limits = result;
            out.limitsFrom = DeviceConfig::LimitsFrom::file;
            return Result::ok();
        }

        Result parseObject (const juce::var& json, DeviceConfig& out)
        {
            if (! json.isObject())
                return Result::fail ("not a JSON object");

            if (auto r = onlyKnownKeys (json, { "version", "role", "amplifier", "limits" }, ""); r.failed())
                return r;

            // As an int64: a version of 2^32 + 1 read as an int would pass for 1.
            if (const auto& version = json["version"]; ! version.isVoid() && (! (version.isInt() || version.isInt64()) || (juce::int64) version != 1))
                return Result::fail ("unsupported version " + version.toString() + " (this speakerd reads version 1)");

            String role;
            if (auto r = readString (json, "role", role, true); r.failed())
                return r;

            if      (role == "left")  out.role = Role::left;
            else if (role == "right") out.role = Role::right;
            else if (role == "mono")  out.role = Role::mono;
            else return Result::fail ("'role' must be \"left\", \"right\" or \"mono\", not \"" + role + "\"");

            if (auto r = parseLimits (json, out); r.failed())
                return r;

            const auto& amplifier = json["amplifier"];

            if (amplifier.isString())
                return readString (json, "amplifier", out.amplifierNode, true);

            if (! amplifier.isObject())
                return Result::fail ("'amplifier' is missing: a node name, or { \"node\": ..., \"woofer\": ..., \"tweeter\": ... }");

            if (auto r = onlyKnownKeys (amplifier, { "node", "woofer", "tweeter" }, "amplifier: "); r.failed())
                return r;

            for (auto [key, target, required] : { std::tuple { "node",    &out.amplifierNode, true },
                                                  std::tuple { "woofer",  &out.wooferPort,    false },
                                                  std::tuple { "tweeter", &out.tweeterPort,   false } })
                if (auto r = readString (amplifier, key, *target, required); r.failed())
                    return Result::fail ("amplifier: " + r.getErrorMessage());

            if (out.wooferPort == out.tweeterPort)
                return Result::fail ("amplifier: the woofer and the tweeter are both on port " + out.wooferPort);

            return Result::ok();
        }
    }

    juce::String DeviceConfig::Limits::describe() const
    {
        char gain[32];
        std::snprintf (gain, sizeof gain, "%g", gainDb);
        return "output at " + juce::String (gain) + " dB, tweeter " + (tweeterMuted ? "muted" : "on");
    }

    juce::String DeviceConfig::describeLimits() const
    {
        switch (limitsFrom)
        {
            case LimitsFrom::file:        return limits.describe();
            case LimitsFrom::notInFile:   return limits.describe() + " (no limits in device.json: the fail-safe)";
            case LimitsFrom::fileNotRead: return limits.describe() + " (device.json not read: the fail-safe)";
        }

        return limits.describe();
    }

    juce::Result DeviceConfig::parse (const juce::String& text, DeviceConfig& out)
    {
        juce::var json;
        if (const auto r = juce::JSON::parse (text, json); r.failed())
            return r;

        if (const auto repeated = findRepeatedKey (text); repeated.isNotEmpty())
            return juce::Result::fail ("'" + repeated + "' is given twice");

        return parseObject (json, out);
    }

    DeviceConfig DeviceConfig::load (const juce::File& file, juce::String& error)
    {
        error.clear();

        if (! file.existsAsFile())
        {
            error = file.getFullPathName() + " does not exist";
            return {};
        }

        // A device file is a few hundred bytes.
        if (file.getSize() > 64 * 1024)
        {
            error = file.getFileName() + ": larger than 64 kB";
            return {};
        }

        DeviceConfig config;
        if (const auto r = parse (file.loadFileAsString(), config); r.failed())
        {
            error = file.getFileName() + ": " + r.getErrorMessage();
            return {};
        }

        return config;
    }
}
