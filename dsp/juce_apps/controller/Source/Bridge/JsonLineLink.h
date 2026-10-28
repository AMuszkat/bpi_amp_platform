#pragma once

#include <juce_events/juce_events.h>

#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>

namespace bpi
{
    /** A TCP client that speaks JSON lines (docs/protocol.md, "Framing"): one JSON object per
        line. It connects, and reconnects every second while it cannot or after the peer
        closes. A line that does not parse, or one longer than 32 MiB, closes the connection.

        Its own thread does all the socket work, reading and writing; the callbacks run on the
        message thread, in order. send() never touches the socket: it queues the line and wakes
        the link's thread, so the message thread never waits for a peer. The queue is bounded:
        a peer that stops reading until more than `maxQueuedBytes` wait is dropped, and the link
        reconnects (a fresh connection starts with the hub's state). */
    class JsonLineLink final : private juce::Thread,
                               private juce::AsyncUpdater
    {
    public:
        static constexpr size_t defaultMaxQueuedBytes = 4u * 1024u * 1024u;

        JsonLineLink (const juce::String& linkName, const juce::String& host, int port,
                      size_t maxQueuedBytes = defaultMaxQueuedBytes);
        ~JsonLineLink() override;

        void start();

        /** Queues one object as a line; never blocks. False when not connected, or when the
            queue is full (the connection is then dropped and made again). */
        bool send (const juce::var& message);

        bool isConnected() const noexcept   { return connected.load(); }
        juce::String getAddress() const     { return host + ":" + juce::String (port); }
        size_t getQueuedBytes() const;

        /** The local IP address of the connection now (empty when not connected): OSC to the
            hub leaves from it, the address the hub authenticated. */
        juce::String getLocalAddress() const;

        /** The next reconnection waits this long instead of 1 s (after a refusal). */
        void backOffNextReconnect (int milliseconds)   { nextDelayMs = milliseconds; }
        int getConnectionCount() const noexcept   { return connectionCount.load(); }

        std::function<void()> onConnected;                          // send the first lines from here
        std::function<void (const juce::String&)> onDisconnected;   // why
        std::function<void (const juce::var&)> onMessage;

        static constexpr size_t maxLineBytes = 32u * 1024u * 1024u;
        static constexpr int refusalBackOffMs = 5000;   // after {"op":"error","about":"hello"}
        static constexpr size_t kernelSendBufferBytes = 64u * 1024u;   // SO_SNDBUF (Linux doubles it)

    private:
        void run() override;
        void handleAsyncUpdate() override;
        juce::String serviceUntilClosed (int fd);
        bool writeQueued (int fd, juce::String& error);
        void clearQueue();
        void wake();
        void post (int kind, juce::var message, juce::String text = {});

        const juce::String host;
        const int port;
        const size_t maxQueued;

        std::atomic<bool> connected { false };
        std::atomic<int> connectionCount { 0 };
        std::atomic<int> nextDelayMs { 0 };
        mutable std::mutex addressLock;
        juce::String localAddress;

        // Outgoing lines. Producers push at the back; only the link's thread pops the front,
        // and std::deque keeps the front's reference valid while others push.
        mutable std::mutex outLock;
        std::deque<std::string> outQueue;
        size_t outBytes = 0;          // queued, not yet written (the front's written part excluded)
        size_t frontWritten = 0;      // link's thread only
        std::atomic<bool> overflowed { false };

        int wakeFds[2] { -1, -1 };    // a pipe: send() and the destructor wake the link's thread

        struct Event { int kind; juce::var message; juce::String text; };
        juce::CriticalSection eventLock;
        std::deque<Event> events;
    };
}
