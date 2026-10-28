#pragma once

#include "Bridge.h"

namespace bpi
{
    /** The test mode: runs a script of commands against the bridge, one at a time, and prints
        one JSON line per command on stdout (README.md, "Test mode"). A command that waits
        polls until its condition holds or its timeout passes. The first command that fails
        ends the run. */
    class ScriptRunner final : private juce::Timer
    {
    public:
        struct Hooks
        {
            /** Opens (at a screen position, when there is one) or closes a node's plugin window;
                false without a GUI. */
            std::function<bool (juce::uint32 uid, bool open, std::optional<juce::Point<int>>)> showWindow;
            /** Moves and sizes the main window; false without a GUI. */
            std::function<bool (juce::Rectangle<int>)> placeMainWindow;
            /** Writes a picture of every window to a PNG; false without a GUI. */
            std::function<bool (const juce::File&)> screenshot;
        };

        ScriptRunner (Bridge&, PluginGraph&, juce::StringArray lines, Hooks, std::function<void (int exitCode)> onDone);
        ~ScriptRunner() override;

        void start();

        /** Reads a script: one command per line, # starts a comment. */
        static juce::StringArray readScript (const juce::File&, juce::String& error);

    private:
        struct Step
        {
            juce::String line;
            juce::StringArray words;
            int number = 0;
        };

        void timerCallback() override;
        void next();
        void finishStep (bool ok, const juce::var& extra = {}, const juce::String& error = {});

        /** Runs the current step: true when it is done (ok or not), false to poll again. */
        bool run (Step&, bool firstTime);

        juce::uint32 uidArg (const juce::String&) const;
        bool pinArg (const juce::String&, juce::uint32& node, int& channel) const;
        double timeoutArg (int index, double defaultSeconds) const;

        static void print (const juce::var&);

        Bridge& bridge;
        PluginGraph& graph;
        juce::StringArray lines;
        Hooks hooks;
        std::function<void (int)> onDone;

        std::vector<Step> steps;
        size_t current = 0;
        bool started = false;
        double deadlineMs = 0.0;
        int failures = 0;
        int outcomesBefore = 0;            // the bridge's proposal outcomes before the last edit
        juce::uint32 lastAdded = 0;
        juce::var waitDetail;
        double startedMs = 0.0;
    };
}
