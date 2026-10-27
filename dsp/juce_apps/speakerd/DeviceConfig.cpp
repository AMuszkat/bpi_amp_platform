#include "DeviceConfig.h"

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

        juce::Result parse (const juce::var& json, DeviceConfig& out)
        {
            if (! json.isObject())
                return juce::Result::fail ("not a JSON object");

            if (const auto& version = json["version"]; ! version.isVoid() && (! (version.isInt() || version.isInt64()) || (int) version != 1))
                return juce::Result::fail ("unsupported version " + version.toString() + " (this speakerd reads version 1)");

            juce::String role;
            if (auto r = readString (json, "role", role, true); r.failed())
                return r;

            if      (role == "left")  out.role = Role::left;
            else if (role == "right") out.role = Role::right;
            else if (role == "mono")  out.role = Role::mono;
            else return juce::Result::fail ("'role' must be \"left\", \"right\" or \"mono\", not \"" + role + "\"");

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

    DeviceConfig DeviceConfig::load (const juce::File& file, juce::String& error)
    {
        error.clear();

        if (! file.existsAsFile())
        {
            error = file.getFullPathName() + " does not exist";
            return {};
        }

        juce::var json;
        if (const auto r = juce::JSON::parse (file.loadFileAsString(), json); r.failed())
        {
            error = file.getFileName() + ": " + r.getErrorMessage();
            return {};
        }

        DeviceConfig config;
        if (const auto r = parse (json, config); r.failed())
        {
            error = file.getFileName() + ": " + r.getErrorMessage();
            return {};
        }

        return config;
    }
}
