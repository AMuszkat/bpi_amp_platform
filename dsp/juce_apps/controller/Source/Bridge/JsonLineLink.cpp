#include "JsonLineLink.h"

#include "GraphDocument.h"

namespace bpi
{
    namespace
    {
        enum EventKind { eventConnected, eventDisconnected, eventMessage };
    }

    JsonLineLink::JsonLineLink (const juce::String& linkName, const juce::String& h, int p)
        : Thread (linkName), host (h), port (p)
    {
    }

    JsonLineLink::~JsonLineLink()
    {
        signalThreadShouldExit();

        {
            const juce::ScopedLock sl (socketLock);
            if (socket != nullptr)
                socket->close();
        }

        stopThread (4000);
        cancelPendingUpdate();
    }

    void JsonLineLink::start()
    {
        startThread();
    }

    bool JsonLineLink::send (const juce::var& message)
    {
        const auto line = document::toJson (message) + "\n";
        const auto* data = line.toRawUTF8();
        auto remaining = (int) line.getNumBytesAsUTF8();

        const juce::ScopedLock sl (socketLock);

        if (socket == nullptr || ! connected.load())
            return false;

        while (remaining > 0)
        {
            const auto written = socket->write (data, remaining);

            if (written <= 0)
                return false;   // the reader notices the close and reconnects

            data += written;
            remaining -= written;
        }

        return true;
    }

    void JsonLineLink::post (int kind, juce::var message, juce::String text)
    {
        {
            const juce::ScopedLock sl (eventLock);
            events.push_back ({ kind, std::move (message), std::move (text) });
        }

        triggerAsyncUpdate();
    }

    void JsonLineLink::handleAsyncUpdate()
    {
        for (;;)
        {
            Event e;

            {
                const juce::ScopedLock sl (eventLock);

                if (events.empty())
                    return;

                e = std::move (events.front());
                events.pop_front();
            }

            if (e.kind == eventConnected && onConnected != nullptr)
                onConnected();
            else if (e.kind == eventDisconnected && onDisconnected != nullptr)
                onDisconnected (e.text);
            else if (e.kind == eventMessage && onMessage != nullptr)
                onMessage (e.message);
        }
    }

    void JsonLineLink::run()
    {
        while (! threadShouldExit())
        {
            auto s = std::make_unique<juce::StreamingSocket>();

            if (! s->connect (host, port, 1000))
            {
                wait (1000);
                continue;
            }

            {
                const juce::ScopedLock sl (socketLock);
                socket = std::move (s);
                connected = true;
            }

            post (eventConnected, {});
            const auto why = readUntilClosed (*socket);

            {
                const juce::ScopedLock sl (socketLock);
                connected = false;
                socket->close();
                socket.reset();
            }

            if (! threadShouldExit())
            {
                post (eventDisconnected, {}, why);
                wait (1000);
            }
        }
    }

    juce::String JsonLineLink::readUntilClosed (juce::StreamingSocket& s)
    {
        juce::MemoryBlock pending;
        char buffer[65536];

        while (! threadShouldExit())
        {
            const auto ready = s.waitUntilReady (true, 200);

            if (ready < 0)
                return "the connection failed";

            if (ready == 0)
                continue;

            const auto n = s.read (buffer, (int) sizeof (buffer), false);

            if (n <= 0)
                return "the peer closed the connection";   // readable and empty

            pending.append (buffer, (size_t) n);

            // Every complete line in what has arrived.
            for (;;)
            {
                const auto* bytes = static_cast<const char*> (pending.getData());
                const auto* end = bytes + pending.getSize();
                const auto* newline = std::find (bytes, end, '\n');

                if (newline == end)
                {
                    if (pending.getSize() > maxLineBytes)
                    {
                        return "a line longer than 32 MiB";
                    }

                    break;
                }

                const auto line = juce::String::fromUTF8 (bytes, (int) (newline - bytes)).trim();
                pending.removeSection (0, (size_t) (newline - bytes) + 1);

                if (line.isEmpty())
                    continue;

                juce::var message;

                if (juce::JSON::parse (line, message).failed() || ! message.isObject())
                    return "a line that does not parse: " + line.substring (0, 80);   // docs/protocol.md: closes the connection

                post (eventMessage, message);
            }
        }

        return "stopped";
    }
}
