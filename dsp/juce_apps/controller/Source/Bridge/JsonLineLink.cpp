#include "JsonLineLink.h"

#include "GraphDocument.h"

#if JUCE_WINDOWS
 #error "JsonLineLink's socket loop is POSIX (poll, a wake-up pipe): it needs a Windows port"
#endif

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>

#ifndef MSG_NOSIGNAL
 #define MSG_NOSIGNAL 0   // macOS: SIGPIPE is ignored for the whole process (Main.cpp)
#endif

namespace bpi
{
    namespace
    {
        enum EventKind { eventConnected, eventDisconnected, eventMessage };

        void setNonBlocking (int fd)
        {
            const auto flags = fcntl (fd, F_GETFL, 0);
            if (flags >= 0)
                fcntl (fd, F_SETFL, flags | O_NONBLOCK);
        }
    }

    JsonLineLink::JsonLineLink (const juce::String& linkName, const juce::String& h, int p, size_t maxQueuedBytes)
        : Thread (linkName), host (h), port (p), maxQueued (maxQueuedBytes)
    {
        if (pipe (wakeFds) == 0)
        {
            setNonBlocking (wakeFds[0]);
            setNonBlocking (wakeFds[1]);
        }
    }

    JsonLineLink::~JsonLineLink()
    {
        signalThreadShouldExit();
        wake();
        stopThread (4000);
        cancelPendingUpdate();

        for (auto fd : wakeFds)
            if (fd >= 0)
                close (fd);
    }

    void JsonLineLink::start()
    {
        startThread();
    }

    void JsonLineLink::wake()
    {
        if (wakeFds[1] >= 0)
        {
            const char byte = 1;
            [[maybe_unused]] const auto written = write (wakeFds[1], &byte, 1);   // full pipe: already awake
        }
    }

    juce::String JsonLineLink::getLocalAddress() const
    {
        const std::lock_guard<std::mutex> lock (addressLock);
        return localAddress;
    }

    size_t JsonLineLink::getQueuedBytes() const
    {
        const std::lock_guard<std::mutex> lock (outLock);
        return outBytes;
    }

    void JsonLineLink::clearQueue()
    {
        const std::lock_guard<std::mutex> lock (outLock);
        outQueue.clear();
        outBytes = 0;
        frontWritten = 0;
        overflowed = false;
    }

    bool JsonLineLink::send (const juce::var& message)
    {
        if (! connected.load())
            return false;

        auto line = (document::toJson (message) + "\n").toStdString();

        {
            const std::lock_guard<std::mutex> lock (outLock);

            if (overflowed.load())
                return false;

            // One line always fits an empty queue; beyond the bound the peer has stopped
            // reading, and waiting for it is not an option on the message thread.
            if (outBytes > 0 && outBytes + line.size() > maxQueued)
            {
                overflowed = true;
            }
            else
            {
                outBytes += line.size();
                outQueue.push_back (std::move (line));
            }
        }

        wake();
        return ! overflowed.load();
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
            juce::StreamingSocket s;

            if (! s.connect (host, port, 1000))
            {
                wait (1000);
                continue;
            }

            const auto fd = s.getRawSocketHandle();
            setNonBlocking (fd);

            // A bounded kernel buffer: otherwise Linux grows it to megabytes for a peer that
            // stopped reading, and those stale lines would sit beyond the queue's bound.
            const int sendBuffer = (int) kernelSendBufferBytes;
            setsockopt (fd, SOL_SOCKET, SO_SNDBUF, &sendBuffer, sizeof (sendBuffer));

            {
                sockaddr_storage local {};
                socklen_t length = sizeof (local);
                char text[INET6_ADDRSTRLEN] {};

                if (getsockname (fd, (sockaddr*) &local, &length) == 0)
                {
                    if (local.ss_family == AF_INET)
                        inet_ntop (AF_INET, &((sockaddr_in*) &local)->sin_addr, text, sizeof (text));
                    else if (local.ss_family == AF_INET6)
                        inet_ntop (AF_INET6, &((sockaddr_in6*) &local)->sin6_addr, text, sizeof (text));
                }

                const std::lock_guard<std::mutex> lock (addressLock);
                localAddress = text;
            }

            clearQueue();   // nothing of an earlier connection goes out on this one
            ++connectionCount;
            connected = true;
            post (eventConnected, {});

            const auto why = serviceUntilClosed (fd);

            connected = false;
            s.close();
            clearQueue();

            {
                const std::lock_guard<std::mutex> lock (addressLock);
                localAddress.clear();
            }

            if (! threadShouldExit())
            {
                post (eventDisconnected, {}, why);
                const auto delay = nextDelayMs.exchange (0);
                wait (delay > 0 ? delay : 1000);
            }
        }
    }

    bool JsonLineLink::writeQueued (int fd, juce::String& error)
    {
        for (;;)
        {
            const std::string* front = nullptr;

            {
                const std::lock_guard<std::mutex> lock (outLock);

                if (outQueue.empty())
                    return true;

                front = &outQueue.front();
            }

            const auto remaining = front->size() - frontWritten;
            const auto n = ::send (fd, front->data() + frontWritten, remaining, MSG_NOSIGNAL | MSG_DONTWAIT);

            if (n < 0)
            {
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
                    return true;   // the kernel's buffer is full: poll says when it drains

                error = "writing failed: " + juce::String (std::strerror (errno));
                return false;
            }

            const std::lock_guard<std::mutex> lock (outLock);
            outBytes -= (size_t) n;
            frontWritten += (size_t) n;

            if (frontWritten == front->size())
            {
                outQueue.pop_front();
                frontWritten = 0;
            }
            else
            {
                return true;   // a partial write: the buffer is full
            }
        }
    }

    juce::String JsonLineLink::serviceUntilClosed (int fd)
    {
        juce::MemoryBlock pending;
        char buffer[65536];

        while (! threadShouldExit())
        {
            if (overflowed.load())
                return "the peer stopped reading: more than " + juce::String ((juce::int64) maxQueued)
                       + " bytes were waiting to be sent";

            const auto haveOutput = getQueuedBytes() > 0;

            pollfd fds[2] {};
            fds[0].fd = fd;
            fds[0].events = (short) (POLLIN | (haveOutput ? POLLOUT : 0));
            fds[1].fd = wakeFds[0];
            fds[1].events = POLLIN;

            const auto ready = poll (fds, wakeFds[0] >= 0 ? 2 : 1, 200);

            if (ready < 0)
            {
                if (errno == EINTR)
                    continue;

                return "poll failed: " + juce::String (std::strerror (errno));
            }

            if ((fds[1].revents & POLLIN) != 0)
            {
                char drain[256];
                while (read (wakeFds[0], drain, sizeof (drain)) > 0) {}
            }

            if ((fds[0].revents & (POLLERR | POLLNVAL)) != 0)
                return "the connection failed";

            if ((fds[0].revents & POLLOUT) != 0 || (haveOutput && (fds[0].revents & POLLIN) == 0))
            {
                juce::String error;
                if (! writeQueued (fd, error))
                    return error;
            }

            if ((fds[0].revents & (POLLIN | POLLHUP)) == 0)
                continue;

            const auto n = ::recv (fd, buffer, sizeof (buffer), MSG_DONTWAIT);

            if (n == 0)
                return "the peer closed the connection";

            if (n < 0)
            {
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)
                    continue;

                return "reading failed: " + juce::String (std::strerror (errno));
            }

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
                        return "a line longer than 32 MiB";

                    break;
                }

                const auto line = juce::String::fromUTF8 (bytes, (int) (newline - bytes)).trim();
                pending.removeSection (0, (size_t) (newline - bytes) + 1);

                if (line.isEmpty())
                    continue;

                juce::var message;

                if (juce::JSON::parse (line, message).failed() || ! message.isObject())
                    return "a line that does not parse: " + line.substring (0, 80);   // docs/protocol.md: closes the connection

                // A refused hello (docs/protocol.md, "Authentication"): the peer closes next, and
                // asking again at once would only be refused again.
                if (message["op"].toString() == "error" && message["about"].toString() == "hello")
                    nextDelayMs = refusalBackOffMs;

                post (eventMessage, message);
            }
        }

        return "stopped";
    }
}
