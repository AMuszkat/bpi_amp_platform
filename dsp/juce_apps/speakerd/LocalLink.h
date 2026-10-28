#pragma once

#include <juce_core/juce_core.h>

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include <sys/types.h>

namespace speakerd
{
    /*  The local link to ampd (docs/protocol.md): a Unix stream socket, by default
        $XDG_RUNTIME_DIR/speakerd/control.sock, mode 0600, carrying JSON lines.

        speakerd listens and accepts one client at a time; a second one is told
        {"op":"error","reason":"busy"} and closed. A line longer than maxLineBytes, or one that
        is not JSON, closes the connection (the peer reconnects).

        One thread of its own, at normal priority, does all the socket I/O: it accepts, reads
        and splits lines, hands each JSON object to the Handler (on this thread), and writes
        what anybody queued with send(). Writes never block: what the peer has not read yet
        waits in a buffer; a peer that lets maxBacklogBytes pile up is disconnected, and
        sendValue() drops a value once valueBacklogBytes are waiting (a monitor value is a
        snapshot: the next one replaces it). The audio thread never touches any of this.

        Every connection has a serial number, given to the Handler. Lines are sent to one
        connection: a line for a connection that has gone is dropped, so an answer to a request
        of a client that went never reaches the next one.

        It opens no network socket. */
    class LocalLink
    {
    public:
        static constexpr size_t maxLineBytes      = 32u * 1024u * 1024u;
        static constexpr size_t maxBacklogBytes   = 64u * 1024u * 1024u;
        static constexpr size_t valueBacklogBytes = 256u * 1024u;

        struct Handler
        {
            virtual ~Handler() = default;

            /** A client connected (link thread). Send the hello from here: it is the first line. */
            virtual void connected (juce::uint64 connection) = 0;

            /** A JSON object arrived (link thread). */
            virtual void received (juce::uint64 connection, const juce::var& message) = 0;

            /** The client went (link thread). */
            virtual void disconnected (juce::uint64 connection) = 0;
        };

        explicit LocalLink (Handler&);
        ~LocalLink();

        /** $XDG_RUNTIME_DIR/speakerd/control.sock, or empty without XDG_RUNTIME_DIR. */
        static juce::String defaultPath();

        /** Listens on `path` and starts the thread. Fails, with the reason, when the path is too
            long, its folder cannot be made, something that is not a socket is there, or another
            process listens there (a socket nobody listens on is stale: it is replaced). The
            folder is made, mode 0700, if it is missing; the socket is mode 0600. */
        juce::Result start (const juce::String& path);

        /** Closes the connection and the socket, and removes the socket file, if it is still
            ours. */
        void stop();

        /** Any thread: sends one line (no newline in it) to `connection`, if it is the current
            one. */
        void send (juce::uint64 connection, const juce::String& line);

        /** The same, for a monitor value: dropped when the peer is behind. */
        void sendValue (juce::uint64 connection, const juce::String& line);

        juce::uint64 currentConnection() const noexcept   { return current.load(); }

    private:
        void run();
        void accept();
        bool readClient();                        // false: the connection is over
        bool handleLines (size_t searchFrom);     // false: the connection is to be closed
        bool flush();                             // false: the connection is over
        void closeClient (const juce::String& why);
        void wake();
        void enqueue (juce::uint64 connection, const juce::String& line, size_t limit);

        Handler& handler;
        juce::String path;
        int listenFd = -1, wakeFd = -1, clientFd = -1;
        dev_t socketDevice = 0;
        ino_t socketInode = 0;
        std::thread thread;
        std::atomic<bool> stopping { false };

        std::atomic<juce::uint64> current { 0 };
        juce::uint64 nextConnection = 0;

        std::string input;                        // link thread: bytes read, not yet a whole line
        std::string writing;                      // link thread: bytes taken from `pending`, not yet written

        std::mutex outputLock;                    // guards pending and tooFar
        std::string pending;
        bool tooFar = false;                      // the peer let maxBacklogBytes pile up
        std::atomic<size_t> backlog { 0 };        // pending + writing, roughly

        JUCE_DECLARE_NON_COPYABLE (LocalLink)
    };
}
