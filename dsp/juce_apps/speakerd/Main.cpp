// speakerd: the headless plugin host that runs on each speaker (docs/remote-plugin-host.md,
// section 2). It runs a graph of headless plugin builds inside the PipeWire cycle, from the first
// state layer that loads, with no network, no X11 and no controller. See README.md.
//
//     speakerd --state-dir DIR [--node-name NAME] [--from LAYER] [--lv2-bundle PATH]...
//     speakerd --state-dir DIR --render IN.wav OUT.wav [--block N] [--from LAYER] [--lv2-bundle PATH]...

#include "DeviceConfig.h"
#include "Engine.h"
#include "GraphBuilder.h"
#include "Log.h"
#include "PipeWireNode.h"
#include "Render.h"
#include "StateLayers.h"

#include <atomic>
#include <csignal>
#include <iostream>
#include <pthread.h>
#include <thread>

namespace
{
    using namespace speakerd;

    /** Exit status (README.md). Passthrough is not an error: speakerd plays. */
    enum ExitCode
    {
        exitOk       = 0,   // stopped by SIGINT/SIGTERM/SIGHUP, at any point, or the render was written
        exitFailure  = 1,   // anything else that stops speakerd
        exitUsage    = 2,   // bad command line
        exitPipeWire = 3,   // the input sink or the filter could not be started, or PipeWire was lost
        exitRender   = 4    // the render could not read IN.wav or write OUT.wav
    };

    constexpr int defaultBlock = 1024;          // the speaker's quantum
    constexpr int defaultQuantumLimit = 8192;   // PipeWire's default.clock.quantum-limit

    std::atomic<bool> quitRequested { false };

    struct Options
    {
        juce::File stateDir;
        juce::String nodeName = "speakerd";
        juce::String firstLayer = "current";
        juce::StringArray bundles;
        bool render = false;
        juce::File renderIn, renderOut;
        int block = defaultBlock;
        bool blockGiven = false;
    };

    void printUsage()
    {
        std::cerr << "usage: speakerd --state-dir DIR [--node-name NAME] [--from LAYER] [--lv2-bundle PATH]...\n"
                     "       speakerd --state-dir DIR --render IN.wav OUT.wav [--block N] [--from LAYER] [--lv2-bundle PATH]...\n"
                     "\n"
                     "  --state-dir DIR     graph.json, graph.lkg.json, factory.json and device.json\n"
                     "  --node-name NAME    the sink players play into (default speakerd); the graph runs in NAME-dsp\n"
                     "  --from LAYER        start trying at this layer: current (default), last-known-good,\n"
                     "                      factory or passthrough\n"
                     "  --lv2-bundle PATH   also load this LV2 bundle (repeatable); plugins are\n"
                     "                      otherwise found by URI in ~/.lv2, /usr/lib/lv2, /usr/local/lib/lv2\n"
                     "  --render IN OUT     no PipeWire: run IN.wav through the graph, write the woofer\n"
                     "                      and tweeter channels to OUT.wav\n"
                     "  --block N           the render's block size (default 1024, the live quantum);\n"
                     "                      render only: the live node runs at PipeWire's quantum\n";
    }

    juce::File absoluteFile (const juce::String& path)
    {
        return juce::File::getCurrentWorkingDirectory().getChildFile (path);
    }

    bool parseArguments (int argc, char* argv[], Options& options)
    {
        for (int i = 1; i < argc; ++i)
        {
            const juce::String arg (argv[i]);
            const auto hasValues = [&] (int count) { return i + count < argc; };

            if (arg == "--help" || arg == "-h")
            {
                printUsage();
                std::exit (exitOk);
            }
            else if (arg == "--state-dir" && hasValues (1))
            {
                options.stateDir = absoluteFile (argv[++i]);
            }
            else if (arg == "--node-name" && hasValues (1))
            {
                options.nodeName = argv[++i];
            }
            else if (arg == "--from" && hasValues (1))
            {
                options.firstLayer = argv[++i];

                if (! layerNames().contains (options.firstLayer))
                {
                    std::cerr << "speakerd: --from takes " << layerNames().joinIntoString (", ") << "\n";
                    return false;
                }
            }
            else if (arg == "--lv2-bundle" && hasValues (1))
            {
                options.bundles.add (absoluteFile (argv[++i]).getFullPathName());
            }
            else if (arg == "--render" && hasValues (2))
            {
                options.render = true;
                options.renderIn = absoluteFile (argv[++i]);
                options.renderOut = absoluteFile (argv[++i]);
            }
            else if (arg == "--block" && hasValues (1))
            {
                options.block = juce::String (argv[++i]).getIntValue();
                options.blockGiven = true;

                if (options.block < 1 || options.block > 65536)
                {
                    std::cerr << "speakerd: --block takes a size from 1 to 65536\n";
                    return false;
                }
            }
            else
            {
                std::cerr << "speakerd: unknown or incomplete argument " << arg << "\n";
                return false;
            }
        }

        if (options.blockGiven && ! options.render)
        {
            std::cerr << "speakerd: --block is for the render only: the live node runs at PipeWire's quantum\n";
            return false;
        }

        if (options.stateDir == juce::File())
        {
            std::cerr << "speakerd: --state-dir is required\n";
            return false;
        }

        if (! options.stateDir.isDirectory())
        {
            std::cerr << "speakerd: " << options.stateDir.getFullPathName() << " is not a directory\n";
            return false;
        }

        return true;
    }

    DeviceConfig loadDevice (const juce::File& stateDir)
    {
        juce::String error;
        auto device = DeviceConfig::load (stateDir.getChildFile ("device.json"), error);

        if (error.isNotEmpty())
            log ("device.json: FAILED: " + error + "; using the defaults");

        log ("device: role " + toString (device.role) + ", amplifier '" + device.amplifierNode + "' (woofer "
             + device.wooferPort + ", tweeter " + device.tweeterPort + ")");
        return device;
    }

    bool loadBundles (juce::AudioPluginFormatManager& formats, const juce::StringArray& bundles)
    {
        for (const auto& bundle : bundles)
        {
            if (! juce::File (bundle).isDirectory())
            {
                log ("--lv2-bundle " + bundle + ": not a directory");
                return false;
            }

            juce::OwnedArray<juce::PluginDescription> found;

            for (auto* format : formats.getFormats())
                if (format->getName() == "LV2")
                    format->findAllTypesForFile (found, bundle);

            if (found.isEmpty())
            {
                // lilv keeps the first plugin of a URI it sees, and the default paths come first.
                log ("--lv2-bundle " + bundle + ": no LV2 plugin loaded from it: either it holds none, or a plugin "
                     "with the same URI in ~/.lv2, /usr/lib/lv2 or /usr/local/lib/lv2 shadows it");
                return false;
            }

            log ("lv2 bundle " + bundle + ": " + found.getFirst()->fileOrIdentifier);
        }

        return true;
    }

    //==============================================================================
    /** Watches the live node from the message thread: a stop request, the audio thread's
        requests, the links to the amplifier, and the "playing" line. */
    class Controller final : private juce::Timer
    {
    public:
        Controller (Engine& e, PipeWireNode& n, const juce::String& layerIn)
            : engine (e), node (n), layer (layerIn)
        {
            startTimer (20);
        }

        ~Controller() override { stopTimer(); }

        int getExitCode() const noexcept { return exitCode; }

    private:
        void timerCallback() override
        {
            // The signal thread only sets the flag; stopping is done here, on the message thread.
            if (quitRequested.load())
            {
                stopTimer();
                return juce::MessageManager::getInstance()->stopDispatchLoop();
            }

            if (node.hasFailed())
            {
                log ("lost PipeWire");
                exitCode = exitPipeWire;
                stopTimer();
                return juce::MessageManager::getInstance()->stopDispatchLoop();
            }

            if (const auto changes = engine.getQuantumChanges(); changes != seenQuantumChanges)
            {
                seenQuantumChanges = changes;
                const auto quantum = engine.getLastBlockSize();
                const auto primed = engine.getPrimedBlockSize();

                // A quantum that is not a whole number of primed blocks is re-primed by service(),
                // which says so.
                if (primed > 0 && quantum % primed == 0)
                    log ("pipewire: the quantum changed to " + juce::String (quantum) + ": the graph runs it as "
                         + juce::String (quantum / primed) + " block(s) of " + juce::String (primed));
            }

            const auto serviced = engine.service();

            if (serviced.rate > 0.0)
                log ("pipewire: the graph now runs at " + juce::String (juce::roundToInt (serviced.rate))
                     + " Hz: re-prepared the plugins");

            if (serviced.primedBlock > 0)
                log ("pipewire: the quantum changed to " + juce::String (serviced.primedBlock)
                     + ": faded out, primed the graph for it and faded back in");

            if (const auto links = node.getLinkCount(); links != seenLinks)
            {
                seenLinks = links;
                cyclesAtLink = engine.getCycles();
                announcedPlaying = false;
            }

            if (! announcedPlaying && node.isLinked() && engine.getCycles() > cyclesAtLink + 1 && engine.isAudible())
            {
                log ("playing: layer " + layer + ", " + juce::String (juce::roundToInt (engine.getSampleRate()))
                     + " Hz, quantum " + juce::String (engine.getLastBlockSize()));
                announcedPlaying = true;
            }
        }

        Engine& engine;
        PipeWireNode& node;
        const juce::String layer;
        juce::uint64 seenLinks = 0, cyclesAtLink = 0;
        int seenQuantumChanges = 0;
        bool announcedPlaying = false;
        int exitCode = exitOk;
    };

    int runLive (const Options& options, const DeviceConfig& device, juce::AudioPluginFormatManager& formats)
    {
        Engine engine;
        PipeWireNode node (engine, options.nodeName, device);
        PipeWireNode::Clock clock;

        // speakerd starts right after PipeWire; if it is not there yet, wait for it. Every way
        // out says "stopping" once, last.
        for (bool announced = false;;)
        {
            if (quitRequested.load())
            {
                logLast ("stopping");
                return exitOk;
            }

            const auto connected = node.connect (clock);

            if (connected.wasOk())
                break;

            if (! announced)
                log (connected.getErrorMessage() + ": trying again every second");

            announced = true;

            for (int i = 0; i < 10 && ! quitRequested.load(); ++i)
                juce::Thread::sleep (100);
        }

        log ("pipewire: " + juce::String (juce::roundToInt (clock.rate)) + " Hz, quantum " + juce::String (clock.quantum)
             + ", largest quantum " + juce::String (clock.quantumLimit));

        // Prepared for the largest block a cycle can bring; settled at the quantum it will bring.
        const auto maxBlock = juce::jmax (clock.quantumLimit, clock.quantum);
        GraphBuilder builder (formats);
        auto layer = loadFirstWorkingLayer (options.stateDir, builder, device.role, clock.rate, maxBlock, options.firstLayer);
        engine.install (std::move (layer.built.graph), clock.rate, maxBlock, clock.quantum);

        if (quitRequested.load())
        {
            logLast ("stopping");
            node.stop();
            return exitOk;
        }

        if (const auto started = node.start(); started.failed())
        {
            log (started.getErrorMessage());
            node.stopLinking();
            logLast ("stopping");
            node.stop();
            return exitPipeWire;
        }

        int exitCode = exitOk;
        {
            Controller controller (engine, node, layer.describe());
            juce::MessageManager::getInstance()->runDispatchLoop();
            exitCode = controller.getExitCode();
        }

        // No new link, and no line about one, once speakerd says it is stopping: the links that
        // exist stay for the fade-out.
        node.stopLinking();
        logLast ("stopping");
        engine.fadeOut();
        node.stop();
        return exitCode;
    }

    int runRender (const Options& options, const DeviceConfig& device, juce::AudioPluginFormatManager& formats)
    {
        std::unique_ptr<juce::AudioFormatReader> input;

        if (const auto opened = render::openInput (options.renderIn, input); opened.failed())
        {
            log ("render: " + opened.getErrorMessage());
            return exitRender;
        }

        const auto rate = input->sampleRate;
        const auto maxBlock = juce::jmax (defaultQuantumLimit, options.block);

        GraphBuilder builder (formats);
        auto layer = loadFirstWorkingLayer (options.stateDir, builder, device.role, rate, maxBlock, options.firstLayer);

        Engine engine;
        engine.install (std::move (layer.built.graph), rate, maxBlock, options.block, false);

        if (const auto rendered = render::run (engine, *input, options.renderOut, options.block); rendered.failed())
        {
            log ("render: " + rendered.getErrorMessage());
            return exitRender;
        }

        log ("render: " + juce::String (input->lengthInSamples) + " samples at " + juce::String (juce::roundToInt (rate))
             + " Hz in blocks of " + juce::String (options.block) + ", layer " + layer.describe() + ", written to "
             + options.renderOut.getFullPathName());
        return exitOk;
    }

    /** SIGINT, SIGTERM and SIGHUP stop speakerd cleanly, with a fade. They are blocked in every
        thread and taken by this one, which only sets quitRequested: the Controller's timer and
        the wait for PipeWire poll it, on the message thread. */
    void handleSignalsOnOwnThread (const sigset_t& signals)
    {
        std::thread ([signals]
        {
            int signal = 0;

            if (sigwait (&signals, &signal) == 0)
                quitRequested = true;
        }).detach();
    }
}

//==============================================================================
int main (int argc, char* argv[])
{
    Options options;

    if (! parseArguments (argc, argv, options))
    {
        printUsage();
        return exitUsage;
    }

    std::signal (SIGPIPE, SIG_IGN);

    sigset_t signals;
    sigemptyset (&signals);

    if (! options.render)
    {
        // Before any thread starts, so that every thread inherits the mask.
        sigaddset (&signals, SIGINT);
        sigaddset (&signals, SIGTERM);
        sigaddset (&signals, SIGHUP);
        pthread_sigmask (SIG_BLOCK, &signals, nullptr);
    }

    // The message manager only: speakerd has no GUI and opens no display.
    juce::ScopedJuceInitialiser_GUI juceInitialiser;

    if (! options.render)
        handleSignalsOnOwnThread (signals);

    log (juce::String ("speakerd ") + SPEAKERD_VERSION + ", state dir " + options.stateDir.getFullPathName());

    const auto device = loadDevice (options.stateDir);

    juce::AudioPluginFormatManager formats;
    juce::addHeadlessDefaultFormatsToManager (formats);

    if (! loadBundles (formats, options.bundles))
        return exitUsage;

    return options.render ? runRender (options, device, formats)
                          : runLive (options, device, formats);
}
