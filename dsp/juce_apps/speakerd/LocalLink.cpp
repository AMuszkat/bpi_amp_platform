#include "LocalLink.h"

#include "Log.h"

#include <cerrno>
#include <cstring>

#include <fcntl.h>
#include <poll.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

namespace speakerd
{
    namespace
    {
        juce::String errorText (int error)
        {
            return juce::String::fromUTF8 (std::strerror (error));
        }

        bool makeAddress (const juce::String& path, sockaddr_un& address)
        {
            std::memset (&address, 0, sizeof address);
            address.sun_family = AF_UNIX;
            const auto bytes = (size_t) path.getNumBytesAsUTF8();

            if (bytes == 0 || bytes >= sizeof address.sun_path)
                return false;

            std::memcpy (address.sun_path, path.toRawUTF8(), bytes);
            return true;
        }

        /** True when a process accepts connections on the socket at `address`. */
        bool somebodyListens (const sockaddr_un& address)
        {
            const auto fd = ::socket (AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);

            if (fd < 0)
                return false;

            const auto connected = ::connect (fd, reinterpret_cast<const sockaddr*> (&address), sizeof address) == 0;
            ::close (fd);
            return connected;
        }
    }

    LocalLink::LocalLink (Handler& h) : handler (h) {}

    LocalLink::~LocalLink()
    {
        stop();
    }

    juce::String LocalLink::defaultPath()
    {
        const auto runtime = juce::SystemStats::getEnvironmentVariable ("XDG_RUNTIME_DIR", {});

        if (runtime.isEmpty())
            return {};

        return runtime.trimCharactersAtEnd ("/") + "/speakerd/control.sock";
    }

    juce::Result LocalLink::start (const juce::String& pathIn)
    {
        path = pathIn;
        sockaddr_un address;

        if (! path.startsWithChar ('/'))
            return juce::Result::fail (path + ": not an absolute path");

        if (! makeAddress (path, address))
            return juce::Result::fail (path + ": longer than a Unix socket path can be ("
                                       + juce::String ((int) sizeof address.sun_path - 1) + " bytes)");

        // The folder: made if missing (one level, mode 0700), never changed if it is there.
        const auto folder = path.upToLastOccurrenceOf ("/", false, false);

        if (folder.isNotEmpty() && ::mkdir (folder.toRawUTF8(), 0700) != 0 && errno != EEXIST)
            return juce::Result::fail ("cannot make " + folder + ": " + errorText (errno));

        // Something there already: a socket another process listens on is not ours to take;
        // a socket nobody listens on is what a speakerd that crashed left, and is replaced;
        // anything else is not touched.
        struct stat existing;

        if (::lstat (path.toRawUTF8(), &existing) == 0)
        {
            if (! S_ISSOCK (existing.st_mode))
                return juce::Result::fail (path + " exists and is not a socket");

            if (somebodyListens (address))
                return juce::Result::fail ("another process listens on " + path + " (another speakerd?)");

            ::unlink (path.toRawUTF8());
        }

        listenFd = ::socket (AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);

        if (listenFd < 0)
            return juce::Result::fail ("cannot make a socket: " + errorText (errno));

        const auto failed = [this] (const juce::String& what)
        {
            const auto error = errno;
            ::close (listenFd);
            listenFd = -1;
            return juce::Result::fail (what + ": " + errorText (error));
        };

        if (::bind (listenFd, reinterpret_cast<const sockaddr*> (&address), sizeof address) != 0)
            return failed ("cannot bind " + path);

        // Before listen(): nobody can connect until then, so the mode is right from the first
        // connection on.
        if (::chmod (path.toRawUTF8(), 0600) != 0)
        {
            const auto r = failed ("cannot make " + path + " mode 0600");
            ::unlink (path.toRawUTF8());
            return r;
        }

        struct stat own;

        if (::stat (path.toRawUTF8(), &own) == 0)
        {
            socketDevice = own.st_dev;
            socketInode = own.st_ino;
        }

        if (::listen (listenFd, 4) != 0)
        {
            const auto r = failed ("cannot listen on " + path);
            ::unlink (path.toRawUTF8());
            return r;
        }

        wakeFd = ::eventfd (0, EFD_CLOEXEC | EFD_NONBLOCK);

        if (wakeFd < 0)
        {
            const auto r = failed ("cannot make an eventfd");
            ::unlink (path.toRawUTF8());
            return r;
        }

        stopping = false;
        thread = std::thread ([this] { run(); });
        return juce::Result::ok();
    }

    void LocalLink::stop()
    {
        if (! thread.joinable())
            return;

        stopping = true;
        wake();
        thread.join();

        if (clientFd >= 0)
        {
            ::close (clientFd);
            clientFd = -1;
        }

        current = 0;
        ::close (listenFd);
        ::close (wakeFd);
        listenFd = wakeFd = -1;

        // Only the socket file this speakerd made: a later one may have replaced it.
        struct stat there;

        if (::lstat (path.toRawUTF8(), &there) == 0 && there.st_dev == socketDevice && there.st_ino == socketInode)
            ::unlink (path.toRawUTF8());
    }

    //==============================================================================
    void LocalLink::wake()
    {
        if (wakeFd >= 0)
        {
            const uint64_t one = 1;
            [[maybe_unused]] const auto written = ::write (wakeFd, &one, sizeof one);
        }
    }

    void LocalLink::enqueue (juce::uint64 connection, const juce::String& line, size_t limit)
    {
        if (connection == 0 || connection != current.load())
            return;

        {
            const std::lock_guard<std::mutex> lock (outputLock);

            if (connection != current.load() || tooFar)
                return;

            if (backlog.load() >= limit)
            {
                // A value is dropped; anything else past maxBacklogBytes means the peer does
                // not read, and the link thread closes the connection.
                if (limit != maxBacklogBytes)
                    return;

                tooFar = true;
            }
            else
            {
                const auto before = pending.size();
                pending.append (line.toRawUTF8(), (size_t) line.getNumBytesAsUTF8());
                pending.push_back ('\n');
                backlog += pending.size() - before;
            }
        }

        wake();
    }

    void LocalLink::send (juce::uint64 connection, const juce::String& line)
    {
        enqueue (connection, line, maxBacklogBytes);
    }

    void LocalLink::sendValue (juce::uint64 connection, const juce::String& line)
    {
        enqueue (connection, line, valueBacklogBytes);
    }

    //==============================================================================
    void LocalLink::run()
    {
        while (! stopping.load())
        {
            bool hasOutput = ! writing.empty();

            if (! hasOutput)
            {
                const std::lock_guard<std::mutex> lock (outputLock);
                hasOutput = ! pending.empty() || tooFar;
            }

            pollfd fds[3];
            int n = 0;
            fds[n++] = { wakeFd, POLLIN, 0 };
            fds[n++] = { listenFd, POLLIN, 0 };

            if (clientFd >= 0)
                fds[n++] = { clientFd, (short) (POLLIN | (hasOutput ? POLLOUT : 0)), 0 };

            if (::poll (fds, (nfds_t) n, -1) < 0)
            {
                if (errno == EINTR)
                    continue;

                log ("local link: poll failed: " + errorText (errno) + ": the local link stops; speakerd plays on");
                return;
            }

            if (stopping.load())
                return;

            if ((fds[0].revents & POLLIN) != 0)
            {
                uint64_t count;
                [[maybe_unused]] const auto r = ::read (wakeFd, &count, sizeof count);
            }

            // The client first: when it hung up and a new one came in the same wake-up, the new
            // one is accepted, not refused as busy.
            if (clientFd >= 0 && n == 3 && (fds[2].revents & (POLLIN | POLLHUP | POLLERR)) != 0)
                readClient();

            if ((fds[1].revents & POLLIN) != 0)
                accept();

            if (clientFd >= 0)
            {
                bool closeIt = false;
                {
                    const std::lock_guard<std::mutex> lock (outputLock);
                    closeIt = tooFar;
                }

                if (closeIt)
                {
                    closeClient ("it did not read what speakerd sent (" + juce::String ((juce::int64) maxBacklogBytes / (1024 * 1024))
                                 + " MiB waiting)");
                    continue;
                }

                flush();
            }
        }
    }

    void LocalLink::accept()
    {
        for (;;)
        {
            const auto fd = ::accept4 (listenFd, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);

            if (fd < 0)
                return;

            if (clientFd >= 0)
            {
                // One client at a time. The line is short, and the socket's buffer is empty.
                static constexpr char busy[] = "{\"op\":\"error\",\"reason\":\"busy\"}\n";
                [[maybe_unused]] const auto sent = ::send (fd, busy, sizeof busy - 1, MSG_NOSIGNAL | MSG_DONTWAIT);
                ::close (fd);
                log ("local link: refused a second client: busy");
                continue;
            }

            clientFd = fd;
            input.clear();
            writing.clear();

            const auto connection = ++nextConnection;
            {
                const std::lock_guard<std::mutex> lock (outputLock);
                pending.clear();
                tooFar = false;
                backlog = 0;
                current = connection;
            }

            log ("local link: connected");
            handler.connected (connection);
        }
    }

    bool LocalLink::readClient()
    {
        char buffer[65536];

        for (;;)
        {
            const auto got = ::recv (clientFd, buffer, sizeof buffer, MSG_DONTWAIT);

            if (got > 0)
            {
                // What was there before held no newline: the search starts at the new bytes.
                const auto searchFrom = input.size();
                input.append (buffer, (size_t) got);

                if (! handleLines (searchFrom))
                    return false;

                continue;
            }

            if (got == 0)
            {
                closeClient ({});
                return false;
            }

            if (errno == EINTR)
                continue;

            if (errno == EAGAIN || errno == EWOULDBLOCK)
                return true;

            closeClient (errorText (errno));
            return false;
        }
    }

    bool LocalLink::handleLines (size_t searchFrom)
    {
        size_t start = 0;

        for (;;)
        {
            const auto end = input.find ('\n', juce::jmax (start, searchFrom));

            if (end == std::string::npos)
                break;

            if (end - start > maxLineBytes)
            {
                closeClient ("a line longer than 32 MiB");
                return false;
            }

            auto length = end - start;

            if (length > 0 && input[start + length - 1] == '\r')
                --length;

            if (length > 0)
            {
                const auto text = juce::String::fromUTF8 (input.data() + start, (int) length);
                juce::var message;

                if (juce::JSON::parse (text, message).failed())
                {
                    closeClient ("a line that is not JSON");
                    return false;
                }

                const auto connection = current.load();

                if (message.isObject())
                    handler.received (connection, message);

                if (clientFd < 0)
                    return false;
            }

            start = end + 1;
        }

        input.erase (0, start);

        if (input.size() > maxLineBytes)
        {
            closeClient ("a line longer than 32 MiB");
            return false;
        }

        return true;
    }

    bool LocalLink::flush()
    {
        {
            const std::lock_guard<std::mutex> lock (outputLock);

            if (! pending.empty())
            {
                writing.append (pending);
                pending.clear();
            }
        }

        while (! writing.empty())
        {
            const auto sent = ::send (clientFd, writing.data(), writing.size(), MSG_NOSIGNAL | MSG_DONTWAIT);

            if (sent > 0)
            {
                writing.erase (0, (size_t) sent);
                backlog -= juce::jmin ((size_t) sent, backlog.load());
                continue;
            }

            if (sent < 0 && errno == EINTR)
                continue;

            if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
                return true;

            closeClient (sent < 0 ? errorText (errno) : juce::String ("the connection broke"));
            return false;
        }

        return true;
    }

    void LocalLink::closeClient (const juce::String& why)
    {
        if (clientFd < 0)
            return;

        ::close (clientFd);
        clientFd = -1;
        input.clear();
        writing.clear();

        const auto connection = current.load();
        {
            const std::lock_guard<std::mutex> lock (outputLock);
            pending.clear();
            tooFar = false;
            backlog = 0;
            current = 0;
        }

        log (why.isEmpty() ? juce::String ("local link: disconnected")
                           : "local link: closed the connection: " + why);
        handler.disconnected (connection);
    }
}
