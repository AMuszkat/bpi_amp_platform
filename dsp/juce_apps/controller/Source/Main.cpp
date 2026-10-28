/*
  ==============================================================================

   The controller application. Derived from JUCE's
   extras/AudioPluginHost/Source/HostStartup.cpp (Raw Material Software Limited, JUCE 8;
   AGPLv3 or the JUCE licence), without the plugin scanner, the audio device and the
   document file: see README.md.

  ==============================================================================
*/

#include "UI/MainHostWindow.h"
#include "Bridge/ScriptRunner.h"

#include <csignal>
#include <iostream>

namespace bpi
{
    namespace
    {
        const char* const usage = R"(usage: controller [options]

  --hub HOST             the hub (the left speaker's ampd); also Hub > Connect, and the setting "hub"
  --secret-file FILE     the file holding the pair's secret (mode 600); also Hub > Connect, and
                         the setting "secretFile". The secret itself is never an argument
  --name NAME            this controller's origin (default: ctl-<host name>)
  --osc-port PORT        UDP port for the hub's OSC echoes (default: 0, any free port)
  --control-port PORT    the hub's control link (default 7450)
  --knobs-port PORT      the hub's knobs link, OSC (default 7451)
  --monitor-port PORT    every speaker's monitor link (default 7452)
  --speaker NAME=HOST    where speaker NAME's monitor link is (repeatable; default: the hub's
                         address for the hub's own speaker)
  --monitor-rate HZ      rate asked for monitor values (default 30)
  --script FILE          test mode: run FILE's commands, print JSON lines, exit 0 on success
  --gui                  with --script: show the windows too (screenshots, plugin windows)
  --test-probe           add the test probe plugin (a control build with a monitor, mon_level)
  --send-queue-limit N   bytes a link may hold for a peer that stopped reading before it
                         reconnects (default 4194304)
  --trace                every line in and out on stderr
  --self-test            check HMAC-SHA256 against RFC 4231 and exit
  --version
)";

        struct CommandLine
        {
            Bridge::Options bridge;
            juce::String script;
            juce::String secretFile;
            bool gui = true, scriptGui = false, testProbe = false, version = false, help = false, selfTest = false;
            juce::String error;
        };

        CommandLine parse (const juce::StringArray& args, juce::PropertiesFile* settings)
        {
            CommandLine c;

            if (settings != nullptr)
            {
                c.bridge.hubHost = settings->getValue ("hub");
                c.bridge.name = settings->getValue ("name");
                c.secretFile = settings->getValue ("secretFile");

                for (const auto& entry : juce::StringArray::fromTokens (settings->getValue ("speakers"), ",", ""))
                    if (entry.containsChar ('='))
                        c.bridge.speakerAddresses[entry.upToFirstOccurrenceOf ("=", false, false).trim()]
                            = entry.fromFirstOccurrenceOf ("=", false, false).trim();
            }

            for (int i = 0; i < args.size(); ++i)
            {
                const auto& a = args[i];
                const auto value = [&] { return i + 1 < args.size() ? args[++i] : juce::String(); };
                const auto port = [&] (int& out)
                {
                    const auto v = value();
                    out = v.getIntValue();
                    if (! v.containsOnly ("0123456789") || v.isEmpty() || out > 65535)
                        c.error = a + " takes a port number";
                };

                if (a == "--hub")                   c.bridge.hubHost = value();
                else if (a == "--name")             c.bridge.name = value();
                else if (a == "--osc-port")         port (c.bridge.oscPort);
                else if (a == "--control-port")     port (c.bridge.controlPort);
                else if (a == "--knobs-port")       port (c.bridge.knobsPort);
                else if (a == "--monitor-port")     port (c.bridge.monitorPort);
                else if (a == "--monitor-rate")     c.bridge.monitorRateHz = juce::jlimit (1, 60, value().getIntValue());
                else if (a == "--script")           c.script = value();
                else if (a == "--gui")              c.scriptGui = true;
                else if (a == "--test-probe")       c.testProbe = true;
                else if (a == "--trace")            c.bridge.trace = true;
                else if (a == "--secret-file")      c.secretFile = value();
                else if (a == "--secret")           c.error = "the secret is never given on the command line: put it in a file (mode 600) and give --secret-file";
                else if (a == "--self-test")        c.selfTest = true;
                else if (a == "--send-queue-limit")
                {
                    const auto v = value();
                    if (v.isEmpty() || ! v.containsOnly ("0123456789") || v.getLargeIntValue() < 1024)
                        c.error = "--send-queue-limit takes a number of bytes, 1024 or more";
                    c.bridge.sendQueueLimit = (size_t) v.getLargeIntValue();
                }
                else if (a == "--version")          c.version = true;
                else if (a == "--help" || a == "-h") c.help = true;
                else if (a == "--speaker")
                {
                    const auto v = value();
                    if (! v.containsChar ('='))
                        c.error = "--speaker takes NAME=HOST";
                    c.bridge.speakerAddresses[v.upToFirstOccurrenceOf ("=", false, false)] = v.fromFirstOccurrenceOf ("=", false, false);
                }
                else if (a.isNotEmpty())
                {
                    c.error = "unknown option " + a;
                }
            }

            if (c.bridge.name.isEmpty())
                c.bridge.name = "ctl-" + juce::SystemStats::getComputerName().upToFirstOccurrenceOf (".", false, false);

            if (c.script.isNotEmpty())
                c.gui = c.scriptGui;

            return c;
        }
    }

    //==============================================================================
    class ControllerApp final : public juce::JUCEApplication
    {
    public:
        ControllerApp() = default;

        const juce::String getApplicationName() override       { return "BPI Controller"; }
        const juce::String getApplicationVersion() override    { return CONTROLLER_VERSION; }
        bool moreThanOneInstanceAllowed() override             { return true; }

        void initialise (const juce::String&) override
        {
           #if ! JUCE_WINDOWS
            std::signal (SIGPIPE, SIG_IGN);   // a hub that goes away must not end the controller
           #endif

            juce::PropertiesFile::Options options;
            options.applicationName     = "BPI Controller";
            options.filenameSuffix      = "settings";
            options.osxLibrarySubFolder = "Preferences";
            appProperties.setStorageParameters (options);

            commandLine = parse (getCommandLineParameterArray(), appProperties.getUserSettings());

            if (commandLine.help || commandLine.version || commandLine.error.isNotEmpty())
            {
                if (commandLine.error.isNotEmpty())
                    std::cerr << commandLine.error << "\n";

                if (commandLine.version)
                    std::cout << "controller " << CONTROLLER_VERSION << "\n";
                else
                    std::cerr << usage;

                setApplicationReturnValue (commandLine.error.isNotEmpty() ? 2 : 0);
                quit();
                return;
            }

            if (commandLine.selfTest)
            {
                juce::StringArray report;
                const auto ok = auth::selfTest (report);
                std::cout << report.joinIntoString ("\n") << "\n" << (ok ? "HMAC-SHA256 OK" : "HMAC-SHA256 FAILED") << "\n";
                setApplicationReturnValue (ok ? 0 : 1);
                quit();
                return;
            }

            if (const auto r = loadSecret (commandLine.secretFile, commandLine.bridge); r.failed())
            {
                std::cerr << "[controller] " << r.getErrorMessage() << "\n";
                setApplicationReturnValue (2);
                quit();
                return;
            }

            if (commandLine.testProbe)
                ControlPlugins::enableTestProbe();

            graph = std::make_unique<PluginGraph>();
            startBridge (commandLine.bridge);

            if (commandLine.script.isNotEmpty())
                startScript();
        }

        void shutdown() override
        {
            script = nullptr;
            mainWindow = nullptr;
            bridge = nullptr;
            graph = nullptr;
            appProperties.closeFiles();
        }

        void systemRequestedQuit() override
        {
            if (graph != nullptr)
                graph->closeAnyOpenPluginWindows();

            quit();
        }

    private:
        /** The pair's secret from its file into the bridge's options. Only the reason of a
            failure is printed, never the content. */
        static juce::Result loadSecret (const juce::String& path, Bridge::Options& options)
        {
            options.secret.clear();

            if (path.isEmpty())
            {
                std::cerr << "[controller] no secret file (--secret-file): a hub with a secret will refuse this controller\n";
                return juce::Result::ok();
            }

            juce::String warning;
            const auto file = juce::File::getCurrentWorkingDirectory().getChildFile (path);
            const auto result = auth::readSecretFile (file, options.secret, warning);

            if (warning.isNotEmpty())
                std::cerr << "[controller] " << warning << "\n";

            return result;
        }

        void startBridge (const Bridge::Options& options)
        {
            mainWindow = nullptr;
            bridge = nullptr;   // the old one first: it unhooks itself from the graph
            bridge = std::make_unique<Bridge> (*graph, options);

            if (commandLine.gui)
            {
                mainWindow = std::make_unique<MainHostWindow> (*graph, *bridge, appProperties.getUserSettings());
                mainWindow->onQuitRequested = [this] { systemRequestedQuit(); };
                mainWindow->onConnectRequested = [this] (const juce::String& host, const juce::String& secretFile)
                {
                    auto o = bridge->getOptions();
                    o.hubHost = host;

                    if (const auto r = loadSecret (secretFile, o); r.failed())
                    {
                        juce::AlertWindow::showMessageBoxAsync (juce::MessageBoxIconType::WarningIcon,
                                                                "The pair's secret", r.getErrorMessage());
                        return;
                    }

                    appProperties.getUserSettings()->setValue ("hub", host);
                    appProperties.getUserSettings()->setValue ("secretFile", secretFile);
                    juce::MessageManager::callAsync ([this, o] { startBridge (o); });
                };
            }

            bridge->start();
        }

        void startScript()
        {
            juce::String error;
            const auto lines = ScriptRunner::readScript (juce::File::getCurrentWorkingDirectory().getChildFile (commandLine.script), error);

            if (error.isNotEmpty())
            {
                std::cerr << error << "\n";
                setApplicationReturnValue (2);
                quit();
                return;
            }

            ScriptRunner::Hooks hooks;

            if (commandLine.gui)
            {
                hooks.placeMainWindow = [this] (juce::Rectangle<int> r)
                {
                    if (mainWindow == nullptr)
                        return false;

                    mainWindow->setBounds (r);
                    return true;
                };

                hooks.showWindow = [this] (juce::uint32 uid, bool open, std::optional<juce::Point<int>> position)
                {
                    auto* node = graph->graph.getNodeForId (juce::AudioProcessorGraph::NodeID { uid });

                    if (node == nullptr)
                        return false;

                    if (! open)
                    {
                        graph->closeCurrentlyOpenWindowsFor (node->nodeID);
                        return true;
                    }

                    if (position.has_value())
                    {
                        node->properties.set (PluginWindow::getLastXProp (PluginWindow::Type::normal), position->x);
                        node->properties.set (PluginWindow::getLastYProp (PluginWindow::Type::normal), position->y);
                    }

                    if (auto* w = graph->getOrCreateWindowFor (node, PluginWindow::Type::normal))
                    {
                        w->toFront (true);
                        return true;
                    }

                    return false;
                };

                hooks.screenshot = [] (const juce::File& file) { return screenshot (file); };
            }

            script = std::make_unique<ScriptRunner> (*bridge, *graph, lines, hooks, [this] (int code)
            {
                setApplicationReturnValue (code);
                juce::MessageManager::callAsync ([this] { systemRequestedQuit(); });
            });

            script->start();
        }

        /** Every window of the controller, painted by JUCE where it is on the desktop, into one
            PNG: what the screen shows, without a capture of the X server. */
        static bool screenshot (const juce::File& file)
        {
            auto& desktop = juce::Desktop::getInstance();
            juce::Rectangle<int> area;

            for (int i = 0; i < desktop.getNumComponents(); ++i)
                if (auto* c = desktop.getComponent (i); c->isVisible())
                    area = area.isEmpty() ? c->getScreenBounds() : area.getUnion (c->getScreenBounds());

            if (area.isEmpty())
                return false;

            juce::Image image (juce::Image::RGB, area.getWidth(), area.getHeight(), true);

            {
                juce::Graphics g (image);
                g.fillAll (juce::Colours::black);

                for (int i = 0; i < desktop.getNumComponents(); ++i)   // back to front
                {
                    auto* c = desktop.getComponent (i);

                    if (! c->isVisible())
                        continue;

                    const auto bounds = c->getScreenBounds();
                    g.drawImageAt (c->createComponentSnapshot (c->getLocalBounds(), true, 1.0f),
                                   bounds.getX() - area.getX(), bounds.getY() - area.getY());
                }
            }

            file.deleteFile();
            juce::FileOutputStream out (file);
            juce::PNGImageFormat png;
            return out.openedOk() && png.writeImageToStream (image, out);
        }

        juce::ApplicationProperties appProperties;
        CommandLine commandLine;
        std::unique_ptr<PluginGraph> graph;
        std::unique_ptr<Bridge> bridge;
        std::unique_ptr<MainHostWindow> mainWindow;
        std::unique_ptr<ScriptRunner> script;
    };
}

START_JUCE_APPLICATION (bpi::ControllerApp)
