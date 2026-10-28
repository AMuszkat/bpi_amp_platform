#pragma once

#include <juce_events/juce_events.h>

#include <deque>
#include <functional>
#include <memory>

namespace bpi
{
    /** A TCP client that speaks JSON lines (docs/protocol.md, "Framing"): one JSON object per
        line. It connects, and reconnects every second while it cannot or after the peer
        closes. A line that does not parse, or one longer than 32 MiB, closes the connection.

        Its own thread reads; the callbacks run on the message thread, in order. send() may be
        called from any thread. */
    class JsonLineLink final : private juce::Thread,
                               private juce::AsyncUpdater
    {
    public:
        JsonLineLink (const juce::String& linkName, const juce::String& host, int port);
        ~JsonLineLink() override;

        void start();

        /** Sends one object as a line. False when not connected (nothing is queued). */
        bool send (const juce::var& message);

        bool isConnected() const noexcept   { return connected.load(); }
        juce::String getAddress() const     { return host + ":" + juce::String (port); }

        std::function<void()> onConnected;                     // send the first lines from here
        std::function<void (const juce::String&)> onDisconnected;   // why
        std::function<void (const juce::var&)> onMessage;

        static constexpr size_t maxLineBytes = 32u * 1024u * 1024u;

    private:
        void run() override;
        void handleAsyncUpdate() override;
        juce::String readUntilClosed (juce::StreamingSocket&);
        void post (int kind, juce::var message, juce::String text = {});

        const juce::String host;
        const int port;

        juce::CriticalSection socketLock;   // the pointer, and whole lines on write
        std::unique_ptr<juce::StreamingSocket> socket;
        std::atomic<bool> connected { false };

        struct Event { int kind; juce::var message; juce::String text; };
        juce::CriticalSection eventLock;
        std::deque<Event> events;
    };
}
