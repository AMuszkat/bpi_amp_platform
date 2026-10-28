#include "DeviceConfig.h"

#include <cmath>
#include <cstdio>

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
        juce::Result readString (const juce::var& object, const char* key, juce::String& out, bool required)
        {
            const auto& value = object[key];

            if (value.isVoid())
                return required ? juce::Result::fail (juce::String ("'") + key + "' is missing")
                                : juce::Result::ok();

            if (! value.isString() || value.toString().isEmpty())
                return juce::Result::fail (juce::String ("'") + key + "' must be a non-empty string");

            out = value.toString();
            return juce::Result::ok();
        }

        /** "limits": { "gain_db": <at most 0>, "tweeter": "muted" or "on" }, both keys required
            and no other: a key this speakerd does not know could be a limit it would not apply,
            so it fails the file, which then falls back to failSafeLimits. */
        juce::Result parseLimits (const juce::var& json, std::optional<DeviceConfig::Limits>& out)
        {
            const juce::String expected = "{ \"gain_db\": <dB, at most 0>, \"tweeter\": \"muted\" or \"on\" }";

            // Present is present, even as null: only a file without the key has no limits.
            if (! json.getDynamicObject()->hasProperty ("limits"))
                return juce::Result::ok();

            const auto& limits = json["limits"];
            auto* object = limits.getDynamicObject();

            if (object == nullptr)
                return juce::Result::fail ("'limits' must be " + expected);

            for (const auto& property : object->getProperties())
                if (property.name.toString() != "gain_db" && property.name.toString() != "tweeter")
                    return juce::Result::fail ("limits: unknown key '" + property.name.toString() + "' (this speakerd knows "
                                               "\"gain_db\" and \"tweeter\")");

            DeviceConfig::Limits result;

            const auto& gain = limits["gain_db"];

            if (! (gain.isInt() || gain.isInt64() || gain.isDouble()))
                return juce::Result::fail ("limits: 'gain_db' " + juce::String (gain.isVoid() ? "is missing" : "must be a number")
                                           + ": the output gain in dB, at most 0");

            result.gainDb = (double) gain;

            if (! std::isfinite (result.gainDb) || result.gainDb > 0.0)
                return juce::Result::fail ("limits: 'gain_db' must be a finite number of dB, at most 0, not " + gain.toString());

            if (std::fpclassify (result.gainDb) == FP_ZERO)
                result.gainDb = 0.0;   // not -0

            const auto& tweeter = limits["tweeter"];

            if (tweeter.isString() && tweeter.toString() == "muted")
                result.tweeterMuted = true;
            else if (tweeter.isString() && tweeter.toString() == "on")
                result.tweeterMuted = false;
            else
                return juce::Result::fail ("limits: 'tweeter' must be \"muted\" or \"on\""
                                           + juce::String (tweeter.isVoid() ? ", and is missing" : ", not " + juce::JSON::toString (tweeter)));

            out = result;
            return juce::Result::ok();
        }

        juce::Result parse (const juce::var& json, DeviceConfig& out)
        {
            if (! json.isObject())
                return juce::Result::fail ("not a JSON object");

            // As an int64: a version of 2^32 + 1 read as an int would pass for 1.
            if (const auto& version = json["version"]; ! version.isVoid() && (! (version.isInt() || version.isInt64()) || (juce::int64) version != 1))
                return juce::Result::fail ("unsupported version " + version.toString() + " (this speakerd reads version 1)");

            juce::String role;
            if (auto r = readString (json, "role", role, true); r.failed())
                return r;

            if      (role == "left")  out.role = Role::left;
            else if (role == "right") out.role = Role::right;
            else if (role == "mono")  out.role = Role::mono;
            else return juce::Result::fail ("'role' must be \"left\", \"right\" or \"mono\", not \"" + role + "\"");

            if (auto r = parseLimits (json, out.limits); r.failed())
                return r;

            const auto& amplifier = json["amplifier"];

            if (amplifier.isString())
                return readString (json, "amplifier", out.amplifierNode, true);

            if (! amplifier.isObject())
                return juce::Result::fail ("'amplifier' is missing: a node name, or { \"node\": ..., \"woofer\": ..., \"tweeter\": ... }");

            for (auto [key, target, required] : { std::tuple { "node",    &out.amplifierNode, true },
                                                  std::tuple { "woofer",  &out.wooferPort,    false },
                                                  std::tuple { "tweeter", &out.tweeterPort,   false } })
                if (auto r = readString (amplifier, key, *target, required); r.failed())
                    return juce::Result::fail ("amplifier: " + r.getErrorMessage());

            return juce::Result::ok();
        }
    }

    juce::String DeviceConfig::Limits::describe() const
    {
        char gain[32];
        std::snprintf (gain, sizeof gain, "%g", gainDb);
        return "output at " + juce::String (gain) + " dB, tweeter " + (tweeterMuted ? "muted" : "on");
    }

    DeviceConfig DeviceConfig::failSafe()
    {
        DeviceConfig config;
        config.limits = failSafeLimits;
        return config;
    }

    DeviceConfig DeviceConfig::load (const juce::File& file, juce::String& error)
    {
        error.clear();

        if (! file.existsAsFile())
        {
            error = file.getFullPathName() + " does not exist";
            return failSafe();
        }

        juce::var json;
        if (const auto r = juce::JSON::parse (file.loadFileAsString(), json); r.failed())
        {
            error = file.getFileName() + ": " + r.getErrorMessage();
            return failSafe();
        }

        DeviceConfig config;
        if (const auto r = parse (json, config); r.failed())
        {
            error = file.getFileName() + ": " + r.getErrorMessage();
            return failSafe();
        }

        return config;
    }
}
