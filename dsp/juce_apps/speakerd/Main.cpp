// speakerd: the headless plugin host that runs on each speaker (docs/remote-plugin-host.md,
// section 2). It runs a graph of headless plugin builds inside the PipeWire cycle, from the first
// state layer that loads, with no network, no X11 and no controller. See README.md.
//
//     speakerd --state-dir DIR [--node-name NAME] [--from LAYER] [--control-socket PATH | --no-control-socket]
//              [--allow-tweeter-without-crossover] [--lv2-bundle PATH]...
//     speakerd --state-dir DIR --render IN.wav OUT.wav [--block N] [--from LAYER] [--lv2-bundle PATH]...
//     speakerd --import-filtergraph FILE [--device DEVICE_JSON] [--prefer-headless]
//              [--allow-unsafe-routing] [--lv2-bundle PATH]...
//     speakerd --version

#include "BuildInfo.h"    // SPEAKERD_COMMIT, written at every build (BuildInfo.cmake)
#include "Control.h"
#include "DeviceConfig.h"
#include "Engine.h"
#include "FilterGraph.h"
#include "GraphBuilder.h"
#include "LocalLink.h"
#include "Log.h"
#include "Monitor.h"
#include "PipeWireNode.h"
#include "PluginBundles.h"
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
        exitUsage    = 2,   // bad command line, or --import-filtergraph failed
        exitPipeWire = 3,   // the input sink or the filter could not be started, or PipeWire was lost
        exitRender   = 4    // the render could not read IN.wav or write OUT.wav
    };

    /** "0.2.1 (abc123def456)": the version, and the commit it was built from ("-dirty" when the
        tree had changes). An installer reads the version as the first number after "speakerd ". */
    juce::String versionText()
    {
        return juce::String (SPEAKERD_VERSION) + " (" + SPEAKERD_COMMIT + ")";
    }

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
        juce::File importFile;          // --import-filtergraph
        juce::File importDevice;        // --device
        bool preferHeadless = false, allowUnsafeRouting = false;
        juce::String controlSocket;     // --control-socket; empty: the default path
        bool noControlSocket = false;   // --no-control-socket
        bool allowTweeterWithoutCrossover = false;   // the virtual pair's measurements only
        juce::StringArray layerOptions; // the options of the live mode and the render that were given
        juce::StringArray importOptions;
        juce::StringArray liveOptions;  // the options of the live mode only
    };

    void printUsage()
    {
        std::cerr << "usage: speakerd --state-dir DIR [--node-name NAME] [--from LAYER]\n"
                     "                [--control-socket PATH | --no-control-socket] [--allow-tweeter-without-crossover]\n"
                     "                [--lv2-bundle PATH]...\n"
                     "       speakerd --state-dir DIR --render IN.wav OUT.wav [--block N] [--from LAYER] [--lv2-bundle PATH]...\n"
                     "       speakerd --import-filtergraph FILE [--device DEVICE_JSON] [--prefer-headless]\n"
                     "                [--allow-unsafe-routing] [--lv2-bundle PATH]...\n"
                     "       speakerd --version\n"
                     "\n"
                     "  --state-dir DIR     graph.json, graph.lkg.json, factory.json and device.json\n"
                     "  --node-name NAME    the sink players play into (default speakerd); the graph runs in NAME-dsp\n"
                     "  --from LAYER        start trying at this layer: current (default), last-known-good,\n"
                     "                      factory or passthrough\n"
                     "  --control-socket PATH\n"
                     "                      the local link to ampd: listen on this Unix socket instead of\n"
                     "                      $XDG_RUNTIME_DIR/speakerd/control.sock\n"
                     "  --no-control-socket no local link: nothing changes the graph while speakerd runs\n"
                     "  --allow-tweeter-without-crossover\n"
                     "                      with the tweeter on, load a document that feeds it other than through a\n"
                     "                      crossover's high band, with a warning (for measurements, never a speaker)\n"
                     "  --lv2-bundle PATH   also load this LV2 bundle (repeatable); plugins are\n"
                     "                      otherwise found by URI in ~/.lv2, /usr/lib/lv2, /usr/local/lib/lv2\n"
                     "  --render IN OUT     no PipeWire: run IN.wav through the graph, write the woofer\n"
                     "                      and tweeter channels to OUT.wav\n"
                     "  --block N           the render's block size (default 1024, the live quantum);\n"
                     "                      render only: the live node runs at PipeWire's quantum\n"
                     "  --import-filtergraph FILE\n"
                     "                      write an AudioPluginHost session as a graph document to stdout\n"
                     "  --device DEVICE_JSON\n"
                     "                      with --import-filtergraph: which of the session's output channels\n"
                     "                      (playback_FL, playback_FR) is the woofer; without, playback_FL\n"
                     "  --prefer-headless   with --import-filtergraph: a complete or control build's URI becomes\n"
                     "                      its _headless sibling's, when that plugin is found\n"
                     "  --allow-unsafe-routing\n"
                     "                      with --import-filtergraph: import the stream straight to the tweeter,\n"
                     "                      or a control build, with a warning instead of failing\n"
                     "  --version           print speakerd's version and exit\n";
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

            if (arg == "--state-dir" || arg == "--node-name" || arg == "--from" || arg == "--render" || arg == "--block")
                options.layerOptions.addIfNotAlreadyThere (arg);

            if (arg == "--device" || arg == "--prefer-headless" || arg == "--allow-unsafe-routing")
                options.importOptions.addIfNotAlreadyThere (arg);

            if (arg == "--allow-tweeter-without-crossover")
                options.layerOptions.addIfNotAlreadyThere (arg);

            if (arg == "--control-socket" || arg == "--no-control-socket")
            {
                options.liveOptions.addIfNotAlreadyThere (arg);
                options.layerOptions.addIfNotAlreadyThere (arg);
            }

            if (arg == "--help" || arg == "-h")
            {
                printUsage();
                std::exit (exitOk);
            }
            else if (arg == "--version")
            {
                std::cout << "speakerd " << versionText() << std::endl;
                std::exit (std::cout.good() ? exitOk : exitFailure);
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
            else if (arg == "--control-socket" && hasValues (1))
            {
                const juce::String path (argv[++i]);

                if (path.isEmpty())
                {
                    std::cerr << "speakerd: --control-socket takes a path (--no-control-socket turns the local link off)\n";
                    return false;
                }

                options.controlSocket = absoluteFile (path).getFullPathName();
            }
            else if (arg == "--no-control-socket")
            {
                options.noControlSocket = true;
            }
            else if (arg == "--allow-tweeter-without-crossover")
            {
                options.allowTweeterWithoutCrossover = true;
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
            else if (arg == "--import-filtergraph" && hasValues (1))
            {
                options.importFile = absoluteFile (argv[++i]);
            }
            else if (arg == "--device" && hasValues (1))
            {
                options.importDevice = absoluteFile (argv[++i]);
            }
            else if (arg == "--prefer-headless")
            {
                options.preferHeadless = true;
            }
            else if (arg == "--allow-unsafe-routing")
            {
                options.allowUnsafeRouting = true;
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

        if (options.importFile != juce::File())
        {
            if (! options.layerOptions.isEmpty())
            {
                std::cerr << "speakerd: --import-filtergraph takes --device, --prefer-headless, --allow-unsafe-routing "
                             "and --lv2-bundle only, not " << options.layerOptions.joinIntoString (", ") << "\n";
                return false;
            }

            return true;
        }

        if (! options.importOptions.isEmpty())
        {
            std::cerr << "speakerd: " << options.importOptions.joinIntoString (", ") << ": for --import-filtergraph only\n";
            return false;
        }

        if (options.noControlSocket && options.controlSocket.isNotEmpty())
        {
            std::cerr << "speakerd: --control-socket and --no-control-socket: one or the other\n";
            return false;
        }

        if (options.render && ! options.liveOptions.isEmpty())
        {
            std::cerr << "speakerd: " << options.liveOptions.joinIntoString (", ") << ": for the live mode only "
                         "(the render has no local link)\n";
            return false;
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
        log ("device: limits: " + device.describeLimits());
        return device;
    }

    /** The tweeter rule for this device.json and command line (GraphDocument.h). */
    TweeterRule tweeterRuleOf (const DeviceConfig& device, const Options& options)
    {
        TweeterRule rule;
        rule.tweeterOn = ! device.limits.tweeterMuted;
        rule.allowWithoutCrossover = options.allowTweeterWithoutCrossover;

        if (rule.tweeterOn && rule.allowWithoutCrossover)
            log ("*** --allow-tweeter-without-crossover: the tweeter is on, and a document may feed it other than "
                 "through a crossover's high band; for measurements, never on a speaker ***");

        return rule;
    }

    /** device.json's limits as the engine applies them: always some, the fail-safe at least.
        -100 dB or less is silence. */
    OutputLimits outputLimitsOf (const DeviceConfig& device)
    {
        return { (float) juce::Decibels::decibelsToGain (device.limits.gainDb), device.limits.tweeterMuted };
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
                // The default paths come first, and lilv keeps the first plugin of a URI it sees
                // unless a later one is newer (README.md, "Which build runs"): a copy there of the
                // same version or a newer one shadows the bundle; an older one it replaces.
                log ("--lv2-bundle " + bundle + ": no LV2 plugin loaded from it: either it holds none, or a plugin "
                     "with the same URI in ~/.lv2, /usr/lib/lv2 or /usr/local/lib/lv2, of the same version or a "
                     "newer one, shadows it");
                return false;
            }

            log ("lv2 bundle " + bundle + ": " + found.getFirst()->fileOrIdentifier);
        }

        return true;
    }

    /** Which bundle each plugin URI runs from, read the way lilv's world was (PluginBundles.h):
        the LV2 format's default locations, then every --lv2-bundle in order. Right after the
        format and loadBundles read them, so that both see the same files. */
    PluginBundles bundlesOf (juce::AudioPluginFormatManager& formats, const juce::StringArray& lv2Bundles)
    {
        PluginBundles bundles;

        for (auto* format : formats.getFormats())
        {
            if (format->getName() != "LV2")
                continue;

            const auto locations = format->getDefaultLocationsToSearch();
            juce::StringArray raw;

            for (int i = 0; i < locations.getNumPaths(); ++i)
                raw.add (locations.getRawString (i));   // "~/.lv2" as lilv was given it

            bundles.loadDirectories (raw);
        }

        for (const auto& bundle : lv2Bundles)
            bundles.loadBundle (juce::File (bundle));

        return bundles;
    }

    //==============================================================================
    /** Watches the live node from the message thread: a stop request, the audio thread's
        requests, the links to the amplifier, and the "playing" line. */
    class Controller final : private juce::Timer
    {
    public:
        Controller (Engine& e, PipeWireNode& n, Control& c)
            : engine (e), node (n), control (c)
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

            // A waiting graph revision from the local link: built and swapped in here (it takes
            // about a second, the new graph's settling).
            control.service();

            if (quitRequested.load())
                return;

            const auto serviced = engine.service();

            if (serviced.rate > 0.0)
                log ("pipewire: the graph now runs at " + juce::String (juce::roundToInt (serviced.rate))
                     + " Hz: re-prepared the plugins");

            if (serviced.primedBlock > 0)
                log ("pipewire: the quantum changed to " + juce::String (serviced.primedBlock)
                     + ": faded out, primed the graph for it and faded back in");

            // That line says it all: a change the audio thread counted after the check above is
            // not logged again as "runs it as 1 block(s)".
            if (serviced.rate > 0.0 || serviced.primedBlock > 0)
                seenQuantumChanges = engine.getQuantumChanges();

            if (const auto links = node.getLinkCount(); links != seenLinks)
            {
                seenLinks = links;
                cyclesAtLink = engine.getCycles();
                announcedPlaying = false;
            }

            if (! announcedPlaying && node.isLinked() && engine.getCycles() > cyclesAtLink + 1 && engine.isAudible())
            {
                log ("playing: layer " + control.describe() + ", " + juce::String (juce::roundToInt (engine.getSampleRate()))
                     + " Hz, quantum " + juce::String (engine.getLastBlockSize()));
                announcedPlaying = true;
            }
        }

        Engine& engine;
        PipeWireNode& node;
        Control& control;
        juce::uint64 seenLinks = 0, cyclesAtLink = 0;
        int seenQuantumChanges = 0;
        bool announcedPlaying = false;
        int exitCode = exitOk;
    };

    int runLive (const Options& options, const DeviceConfig& device, juce::AudioPluginFormatManager& formats,
                 PluginBundles& bundles)
    {
        Engine engine (outputLimitsOf (device), device.role);
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
        GraphBuilder builder (formats, &bundles);
        const auto tweeterRule = tweeterRuleOf (device, options);
        Control control (engine, builder, versionText(), tweeterRule);
        control.installLayer (loadFirstWorkingLayer (options.stateDir, builder, device.role, tweeterRule, clock.rate,
                                                     maxBlock, options.firstLayer),
                              clock.rate, maxBlock, clock.quantum);

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

        // The local link to ampd, and the monitor thread behind it. speakerd plays whether or not
        // they start, and whether or not anybody connects. The monitor thread runs in any case:
        // it keeps and logs the timing.
        LocalLink link (control);
        Monitor monitor (engine, control, link);
        control.setLink (&link);
        control.setMonitor (&monitor);
        monitor.start();
        bool linkUp = false;

        if (options.noControlSocket)
        {
            log ("local link: off (--no-control-socket)");
        }
        else if (const auto path = options.controlSocket.isNotEmpty() ? options.controlSocket : LocalLink::defaultPath();
                 path.isEmpty())
        {
            log ("local link: FAILED: no XDG_RUNTIME_DIR, and no --control-socket: speakerd plays without it");
        }
        else if (const auto started = link.start (path); started.failed())
        {
            log ("local link: FAILED: " + started.getErrorMessage() + ": speakerd plays without it");
        }
        else
        {
            linkUp = true;
            log ("local link: listening on " + path);
        }

        int exitCode = exitOk;
        {
            Controller controller (engine, node, control);
            juce::MessageManager::getInstance()->runDispatchLoop();
            exitCode = controller.getExitCode();
        }

        // Nothing arrives once speakerd stops: the link closes, then the monitor thread ends.
        if (linkUp)
            link.stop();

        monitor.stop();

        control.setMonitor (nullptr);
        control.setLink (nullptr);

        // No new link, and no line about one, once speakerd says it is stopping: the links that
        // exist stay for the fade-out.
        node.stopLinking();
        logLast ("stopping");
        engine.fadeOut();
        node.stop();
        return exitCode;
    }

    int runRender (const Options& options, const DeviceConfig& device, juce::AudioPluginFormatManager& formats,
                   PluginBundles& bundles)
    {
        std::unique_ptr<juce::AudioFormatReader> input;

        if (const auto opened = render::openInput (options.renderIn, input); opened.failed())
        {
            log ("render: " + opened.getErrorMessage());
            return exitRender;
        }

        const auto rate = input->sampleRate;
        const auto maxBlock = juce::jmax (defaultQuantumLimit, options.block);

        GraphBuilder builder (formats, &bundles);
        auto layer = loadFirstWorkingLayer (options.stateDir, builder, device.role, tweeterRuleOf (device, options), rate,
                                            maxBlock, options.firstLayer);

        Engine engine (outputLimitsOf (device), device.role);
        engine.install ({ std::move (layer.built.graph), nullptr, std::move (layer.built.timedNodes) }, rate, maxBlock,
                        options.block, false);

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

    /** --import-filtergraph: the session as a graph document on stdout, what was mapped on
        stderr. Exit status 0, or 2 with the reason. */
    int runImport (const Options& options, juce::AudioPluginFormatManager& formats)
    {
        const auto failed = [] (const juce::String& reason)
        {
            log ("import: FAILED: " + reason);
            return exitUsage;
        };

        try
        {
            filtergraph::ImportOptions importOptions;
            importOptions.preferHeadless = options.preferHeadless;
            importOptions.allowUnsafeRouting = options.allowUnsafeRouting;
            importOptions.formats = &formats;
            importOptions.note = [] (const juce::String& line) { log ("import: " + line); };

            // The speaker's device file, read as speakerd reads it; but here an invalid one fails.
            DeviceConfig device;

            if (options.importDevice != juce::File())
            {
                juce::String error;
                device = DeviceConfig::load (options.importDevice, error);

                if (error.isNotEmpty())
                    return failed ("--device " + error);

                importOptions.device = &device;
            }

            juce::String document;

            if (const auto imported = filtergraph::import (options.importFile, importOptions, document); imported.failed())
                return failed (imported.getErrorMessage());

            std::cout << document << std::endl;

            if (! std::cout.good())
                return failed ("the document could not be written to stdout");

            return exitOk;
        }
        catch (const std::exception& e)
        {
            return failed (juce::String ("an error: ") + e.what());
        }
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

    const bool importing = options.importFile != juce::File();
    sigset_t signals;
    sigemptyset (&signals);

    if (! options.render && ! importing)
    {
        // Before any thread starts, so that every thread inherits the mask.
        sigaddset (&signals, SIGINT);
        sigaddset (&signals, SIGTERM);
        sigaddset (&signals, SIGHUP);
        pthread_sigmask (SIG_BLOCK, &signals, nullptr);
    }

    // The message manager only: speakerd has no GUI and opens no display.
    juce::ScopedJuceInitialiser_GUI juceInitialiser;

    if (! options.render && ! importing)
        handleSignalsOnOwnThread (signals);

    juce::AudioPluginFormatManager formats;

    if (importing)
    {
        juce::addHeadlessDefaultFormatsToManager (formats);
        return loadBundles (formats, options.bundles) ? runImport (options, formats) : exitUsage;
    }

    log ("speakerd " + versionText() + ", state dir " + options.stateDir.getFullPathName());

    const auto device = loadDevice (options.stateDir);

    juce::addHeadlessDefaultFormatsToManager (formats);

    if (! loadBundles (formats, options.bundles))
        return exitUsage;

    auto bundles = bundlesOf (formats, options.bundles);

    return options.render ? runRender (options, device, formats, bundles)
                          : runLive (options, device, formats, bundles);
}
