#include "PluginBundles.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <set>
#include <sys/stat.h>
#include <unistd.h>

namespace speakerd
{
    namespace
    {
        constexpr const char* rdfType    = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";
        constexpr const char* rdfNil     = "http://www.w3.org/1999/02/22-rdf-syntax-ns#nil";
        constexpr const char* rdfsSeeAlso = "http://www.w3.org/2000/01/rdf-schema#seeAlso";
        constexpr const char* lv2Plugin  = "http://lv2plug.in/ns/lv2core#Plugin";
        constexpr const char* lv2Minor   = "http://lv2plug.in/ns/lv2core#minorVersion";
        constexpr const char* lv2Micro   = "http://lv2plug.in/ns/lv2core#microVersion";
        constexpr const char* xsd        = "http://www.w3.org/2001/XMLSchema#";
        constexpr const char* bpiVersion  = "https://github.com/AMuszkat/bpi_amp_dsp_tuning#version";
        constexpr const char* bpiSourceId = "https://github.com/AMuszkat/bpi_amp_dsp_tuning#sourceId";

        /** A manifest or a plugin's data file larger than this is not read (as if it did not
            parse). LV2 bundles' files are kilobytes; this only bounds the cost of a bad one. */
        constexpr size_t maxTurtleBytes = 16 * 1024 * 1024;

        bool isAlpha (unsigned char c) noexcept  { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
        bool isDigit (unsigned char c) noexcept  { return c >= '0' && c <= '9'; }
        bool isHex (unsigned char c) noexcept    { return isDigit (c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
        int hexValue (unsigned char c) noexcept  { return isDigit (c) ? c - '0' : (std::tolower (c) - 'a' + 10); }

        // Turtle's PN_CHARS, with every byte of a UTF-8 sequence taken as a letter.
        bool isNameStart (unsigned char c) noexcept { return isAlpha (c) || c >= 0x80; }
        bool isNameChar (unsigned char c) noexcept  { return isNameStart (c) || isDigit (c) || c == '_' || c == '-'; }

        //==============================================================================
        /** Reads a regular file of at most maxBytes, without ever waiting on it: the path is
            opened non-blocking and refused before any read unless it is a regular file (a FIFO
            or a device at the path would block, or never end). */
        juce::Result readSmallFile (const juce::File& file, size_t maxBytes, std::string& out)
        {
            const auto path = file.getFullPathName().toStdString();
            const int fd = ::open (path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);

            if (fd < 0)
                return juce::Result::fail (errno == ENOENT ? "no such file" : std::strerror (errno));

            struct stat st {};
            juce::Result result = juce::Result::ok();

            if (::fstat (fd, &st) != 0)
                result = juce::Result::fail (std::strerror (errno));
            else if (! S_ISREG (st.st_mode))
                result = juce::Result::fail ("not a regular file");
            else if ((size_t) st.st_size > maxBytes)
                result = juce::Result::fail (juce::String ((juce::int64) st.st_size) + " bytes, more than "
                                             + juce::String ((juce::int64) maxBytes));

            if (result.wasOk())
            {
                out.assign ((size_t) st.st_size, '\0');
                size_t got = 0;

                while (got < out.size())
                {
                    const auto n = ::read (fd, out.data() + got, out.size() - got);

                    if (n < 0 && errno == EINTR)
                        continue;

                    if (n <= 0)
                        break;

                    got += (size_t) n;
                }

                out.resize (got);   // a file that shrank meanwhile: what there was
            }

            ::close (fd);
            return result;
        }

        juce::Result readTurtle (const juce::File& file, std::vector<turtle::Triple>& out, const turtle::Filter& keep)
        {
            std::string text;

            if (auto r = readSmallFile (file, maxTurtleBytes, text); r.failed())
                return r;

            return turtle::parse (text, turtle::fileIri (file), out, keep);
        }

        //==============================================================================
        /** lilv_expand: a "~" before a slash or at the end is $HOME, and $NAME (capitals, digits,
            '_') its value; a variable that is not set stays as it is written. */
        std::string expandLikeLilv (const std::string& path)
        {
            std::string out;

            for (size_t i = 0; i < path.size();)
            {
                if (path[i] == '$')
                {
                    auto j = i + 1;
                    while (j < path.size() && (std::isupper ((unsigned char) path[j]) || isDigit ((unsigned char) path[j]) || path[j] == '_'))
                        ++j;

                    const auto name = path.substr (i + 1, j - i - 1);
                    const char* value = std::getenv (name.c_str());
                    out += value != nullptr ? std::string (value) : "$" + name;
                    i = j;
                }
                else if (path[i] == '~' && (i + 1 == path.size() || path[i + 1] == '/'))
                {
                    const char* home = std::getenv ("HOME");
                    out += home != nullptr ? std::string (home) : std::string ("$HOME");
                    ++i;
                }
                else
                {
                    out += path[i++];
                }
            }

            return out;
        }

        juce::File fileOfPath (const std::string& path)
        {
            const auto text = juce::String::fromUTF8 (path.data(), (int) path.size());
            return juce::File::isAbsolutePath (text) ? juce::File (text)
                                                     : juce::File::getCurrentWorkingDirectory().getChildFile (text);
        }

        //==============================================================================
        struct Version
        {
            int minor = 0, micro = 0;

            /** lilv_version_cmp. */
            int compare (const Version& other) const noexcept
            {
                if (minor == other.minor && micro == other.micro)
                    return 0;

                return (minor < other.minor || (minor == other.minor && micro < other.micro)) ? -1 : 1;
            }
        };

        /** get_version of lilv's load_plugin_model: the plugin's lv2:minorVersion and
            lv2:microVersion in the bundle's manifest and the plugin's rdfs:seeAlso files (read
            now, as lilv does when two bundles have the plugin); 0.0 unless both are there. */
        Version versionOf (const juce::File& bundle, const std::string& uri)
        {
            const auto aboutTheVersion = [&uri] (const turtle::Triple& t)
            {
                return t.subject.kind == turtle::Term::Kind::iri && t.subject.value == uri
                       && (t.predicate == rdfsSeeAlso || t.predicate == lv2Minor || t.predicate == lv2Micro);
            };

            std::vector<turtle::Triple> triples;
            (void) readTurtle (bundle.getChildFile ("manifest.ttl"), triples, aboutTheVersion);   // what parsed is used, as with serd

            std::vector<std::string> seeAlso;
            for (const auto& t : triples)
                if (t.subject.kind == turtle::Term::Kind::iri && t.subject.value == uri && t.predicate == rdfsSeeAlso
                    && t.object.kind == turtle::Term::Kind::iri)
                    seeAlso.push_back (t.object.value);

            for (const auto& iri : seeAlso)
                if (const auto file = turtle::fileOfIri (iri); file != juce::File())
                    (void) readTurtle (file, triples, aboutTheVersion);

            const std::string* minor = nullptr;
            const std::string* micro = nullptr;

            for (const auto& t : triples)
            {
                if (t.subject.kind != turtle::Term::Kind::iri || t.subject.value != uri)
                    continue;

                if (minor == nullptr && t.predicate == lv2Minor)
                    minor = &t.object.value;
                else if (micro == nullptr && t.predicate == lv2Micro)
                    micro = &t.object.value;
            }

            if (minor == nullptr || micro == nullptr)
                return {};

            return { std::atoi (minor->c_str()), std::atoi (micro->c_str()) };
        }

        //==============================================================================
        bool hasScheme (const std::string& iri)
        {
            if (iri.empty() || ! isAlpha ((unsigned char) iri[0]))
                return false;

            for (size_t i = 1; i < iri.size(); ++i)
            {
                const auto c = (unsigned char) iri[i];

                if (c == ':')
                    return true;

                if (! (isAlpha (c) || isDigit (c) || c == '+' || c == '-' || c == '.'))
                    return false;
            }

            return false;
        }

        /** RFC 3986, 5.2.4. */
        std::string removeDotSegments (std::string in)
        {
            std::string out;
            const auto dropLastSegment = [&out]
            {
                const auto slash = out.rfind ('/');
                out.erase (slash == std::string::npos ? 0 : slash);
            };

            while (! in.empty())
            {
                if (in.rfind ("../", 0) == 0)       in.erase (0, 3);
                else if (in.rfind ("./", 0) == 0)   in.erase (0, 2);
                else if (in.rfind ("/./", 0) == 0)  in.replace (0, 3, "/");
                else if (in == "/.")                in = "/";
                else if (in.rfind ("/../", 0) == 0) { in.replace (0, 4, "/"); dropLastSegment(); }
                else if (in == "/..")               { in = "/"; dropLastSegment(); }
                else if (in == "." || in == "..")   in.clear();
                else
                {
                    auto next = in.find ('/', in[0] == '/' ? 1 : 0);
                    if (next == std::string::npos)
                        next = in.size();

                    out += in.substr (0, next);
                    in.erase (0, next);
                }
            }

            return out;
        }

        /** RFC 3986, 5.2.2: a reference against a base IRI. */
        std::string resolve (const std::string& base, const std::string& ref)
        {
            if (hasScheme (ref))
                return ref;

            const auto withoutFragment = base.substr (0, base.find ('#'));

            if (ref.empty())
                return withoutFragment;

            if (ref[0] == '#')
                return withoutFragment + ref;

            const auto colon = base.find (':');
            const auto scheme = colon == std::string::npos ? std::string() : base.substr (0, colon + 1);
            auto rest = colon == std::string::npos ? withoutFragment : withoutFragment.substr (colon + 1);

            if (ref.rfind ("//", 0) == 0)
                return scheme + ref;

            std::string authority;
            if (rest.rfind ("//", 0) == 0)
            {
                const auto end = rest.find_first_of ("/?", 2);
                authority = rest.substr (0, end);
                rest = end == std::string::npos ? std::string() : rest.substr (end);
            }

            const auto basePath = rest.substr (0, rest.find ('?'));
            const auto cut = ref.find_first_of ("?#");
            const auto refPath = ref.substr (0, cut);
            const auto refRest = cut == std::string::npos ? std::string() : ref.substr (cut);

            std::string path;
            if (refPath.empty())
                path = basePath;
            else if (refPath[0] == '/')
                path = removeDotSegments (refPath);
            else if (! authority.empty() && basePath.empty())
                path = removeDotSegments ("/" + refPath);
            else
                path = removeDotSegments (basePath.substr (0, basePath.rfind ('/') + 1) + refPath);

            return scheme + authority + path + refRest;
        }

        /** A relative reference that resolves to the base's directory followed by itself: a path
            with no "." or ".." segment, no query and no fragment ("dsp.ttl", "presets/a.ttl"). */
        bool isPlainRelativePath (const std::string& ref)
        {
            if (ref.empty() || ref[0] == '/' || ref.find_first_of ("?#") != std::string::npos || hasScheme (ref))
                return false;

            for (size_t start = 0;;)
            {
                const auto end = std::min (ref.find ('/', start), ref.size());
                const auto segment = std::string_view (ref).substr (start, end - start);

                if (segment == "." || segment == "..")
                    return false;

                if (end == ref.size())
                    return true;

                start = end + 1;
            }
        }

        void appendUtf8 (std::string& out, juce::uint32 c)
        {
            if (c < 0x80)
            {
                out += (char) c;
            }
            else if (c < 0x800)
            {
                out += (char) (0xc0 | (c >> 6));
                out += (char) (0x80 | (c & 0x3f));
            }
            else if (c < 0x10000)
            {
                out += (char) (0xe0 | (c >> 12));
                out += (char) (0x80 | ((c >> 6) & 0x3f));
                out += (char) (0x80 | (c & 0x3f));
            }
            else
            {
                out += (char) (0xf0 | (c >> 18));
                out += (char) (0x80 | ((c >> 12) & 0x3f));
                out += (char) (0x80 | ((c >> 6) & 0x3f));
                out += (char) (0x80 | (c & 0x3f));
            }
        }

        //==============================================================================
        /** Turtle (https://www.w3.org/TR/turtle/), by recursive descent over the bytes, at most
            maxDepth blank nodes and collections deep. */
        class Parser
        {
        public:
            Parser (std::string_view textIn, std::string baseIn, std::vector<turtle::Triple>& outIn, const turtle::Filter& keepIn)
                : s (textIn), out (outIn), keep (keepIn)
            {
                setBase (std::move (baseIn));
            }

            juce::Result run()
            {
                if (s.substr (0, 3) == "\xef\xbb\xbf")   // a byte order mark
                    p = 3;

                for (;;)
                {
                    skipSpace();

                    if (! more())
                        return juce::Result::ok();

                    if (! statement())
                        return juce::Result::fail (juce::String::fromUTF8 (error.c_str()) + " (line " + juce::String (line()) + ")");
                }
            }

        private:
            using Term = turtle::Term;
            static constexpr int maxDepth = 32;

            std::string_view s;
            size_t p = 0;
            std::string base;
            std::string baseDirectory;   // what a plain relative path is appended to (isPlainRelativePath)
            std::vector<turtle::Triple>& out;
            const turtle::Filter& keep;
            std::map<std::string, std::string> prefixes;
            std::string error;
            int depth = 0;
            unsigned blanks = 0;

            void setBase (std::string iri)
            {
                base = std::move (iri);
                const auto probe = resolve (base, "x");
                baseDirectory = probe.substr (0, probe.size() - 1);
            }

            bool fail (const std::string& why)
            {
                if (error.empty())
                    error = why;

                return false;
            }

            int line() const   { return 1 + (int) std::count (s.begin(), s.begin() + (std::ptrdiff_t) std::min (p, s.size()), '\n'); }
            bool more() const  { return p < s.size(); }
            unsigned char at (size_t i) const  { return i < s.size() ? (unsigned char) s[i] : 0; }
            unsigned char cur() const          { return at (p); }

            void skipSpace()
            {
                while (more())
                {
                    const auto c = cur();

                    if (c == ' ' || c == '\t' || c == '\r' || c == '\n')
                        ++p;
                    else if (c == '#')
                        while (more() && cur() != '\n')
                            ++p;
                    else
                        break;
                }
            }

            bool expect (char c, const char* where)
            {
                skipSpace();

                if (cur() != (unsigned char) c)
                    return fail (std::string ("expected '") + c + "' " + where);

                ++p;
                return true;
            }

            /** The word here, followed by something that cannot continue a name; consumed. */
            bool keyword (const char* word, bool anyCase)
            {
                const auto n = std::strlen (word);

                for (size_t i = 0; i < n; ++i)
                {
                    auto a = at (p + i), b = (unsigned char) word[i];

                    if (anyCase)
                    {
                        a = (unsigned char) std::tolower (a);
                        b = (unsigned char) std::tolower (b);
                    }

                    if (a != b)
                        return false;
                }

                const auto next = at (p + n);

                if (isNameChar (next) || next == ':' || next == '%' || next == '\\')
                    return false;

                p += n;
                return true;
            }

            bool statement()
            {
                if (cur() == '@')
                {
                    ++p;

                    if (keyword ("prefix", false))
                        return prefixDirective (true);

                    if (keyword ("base", false))
                        return baseDirective (true);

                    return fail ("an unknown directive");
                }

                if (keyword ("PREFIX", true))
                    return prefixDirective (false);

                if (keyword ("BASE", true))
                    return baseDirective (false);

                return triples() && expect ('.', "at the end of a statement");
            }

            bool prefixDirective (bool withDot)
            {
                skipSpace();
                std::string name;

                while (more() && (isNameChar (cur()) || cur() == '.'))
                    name += (char) s[p++];

                if (! name.empty() && (! isNameStart ((unsigned char) name[0]) || name.back() == '.'))
                    return fail ("a prefix name that is not one");

                if (cur() != ':')
                    return fail ("expected ':' after a prefix name");

                ++p;
                skipSpace();

                std::string iri;
                if (! iriRef (iri))
                    return false;

                prefixes[name] = iri;
                return ! withDot || expect ('.', "after @prefix");
            }

            bool baseDirective (bool withDot)
            {
                skipSpace();

                std::string iri;
                if (! iriRef (iri))
                    return false;

                setBase (iri);
                return ! withDot || expect ('.', "after @base");
            }

            /** \uXXXX or \UXXXXXXXX, here. */
            bool unicodeEscape (std::string& text)
            {
                const auto kind = at (p + 1);
                const size_t digits = kind == 'u' ? 4 : kind == 'U' ? 8 : 0;

                if (digits == 0)
                    return fail ("a bad escape");

                juce::uint32 c = 0;

                for (size_t i = 0; i < digits; ++i)
                {
                    const auto h = at (p + 2 + i);

                    if (! isHex (h))
                        return fail ("a bad \\u escape");

                    c = c * 16 + (juce::uint32) hexValue (h);
                }

                if (c > 0x10ffff || (c >= 0xd800 && c <= 0xdfff))
                    return fail ("a \\u escape that is not a character");

                appendUtf8 (text, c);
                p += 2 + digits;
                return true;
            }

            /** <...>, resolved against the base. */
            bool iriRef (std::string& iri)
            {
                if (cur() != '<')
                    return fail ("expected an IRI");

                ++p;
                std::string raw;

                for (;;)
                {
                    if (! more())
                        return fail ("an IRI that does not end");

                    const auto c = cur();

                    if (c == '>')
                    {
                        ++p;
                        break;
                    }

                    if (c <= 0x20 || c == '<' || c == '"' || c == '{' || c == '}' || c == '|' || c == '^' || c == '`')
                        return fail ("a character an IRI cannot hold");

                    if (c == '\\')
                    {
                        if (! unicodeEscape (raw))
                            return false;

                        continue;
                    }

                    raw += (char) c;
                    ++p;
                }

                // Most relative IRIs of a bundle are file names next to it: those need no more than the
                // base's directory in front.
                iri = isPlainRelativePath (raw) ? baseDirectory + raw : resolve (base, raw);
                return true;
            }

            bool prefixedName (std::string& iri)
            {
                std::string prefix;

                while (more() && (isNameChar (cur()) || cur() == '.'))
                    prefix += (char) s[p++];

                if (cur() != ':')
                    return fail (prefix.empty() ? std::string ("unexpected '") + (char) cur() + "'"
                                                : "\"" + prefix + "\" where a term was expected");

                ++p;
                const auto known = prefixes.find (prefix);

                if (known == prefixes.end())
                    return fail ("the prefix \"" + prefix + ":\" is not defined");

                std::string local;
                bool lastEscaped = false;

                while (more())
                {
                    const auto c = cur();

                    if (isNameChar (c) || c == ':' || c == '.')
                    {
                        local += (char) c;
                        ++p;
                        lastEscaped = false;
                    }
                    else if (c == '%' && isHex (at (p + 1)) && isHex (at (p + 2)))
                    {
                        local.append (s.substr (p, 3));
                        p += 3;
                        lastEscaped = false;
                    }
                    else if (c == '\\' && at (p + 1) != 0 && std::strchr ("_~.-!$&'()*+,;=/?#@%", at (p + 1)) != nullptr)
                    {
                        local += (char) at (p + 1);
                        p += 2;
                        lastEscaped = true;
                    }
                    else
                    {
                        break;
                    }
                }

                // A name does not end with a '.': that is the end of the statement.
                while (! lastEscaped && ! local.empty() && local.back() == '.')
                {
                    local.pop_back();
                    --p;
                }

                iri = known->second + local;
                return true;
            }

            bool blankLabel (Term& t)
            {
                p += 2;   // "_:"
                std::string name;

                while (more() && (isNameChar (cur()) || cur() == '.'))
                    name += (char) s[p++];

                while (! name.empty() && name.back() == '.')
                {
                    name.pop_back();
                    --p;
                }

                if (name.empty())
                    return fail ("a blank node without a label");

                t.kind = Term::Kind::blank;
                t.value = "_:" + name;
                return true;
            }

            Term newBlank()
            {
                Term t;
                t.kind = Term::Kind::blank;
                t.value = "[" + std::to_string (++blanks) + "]";
                return t;
            }

            bool blankNodePropertyList (Term& t)
            {
                if (++depth > maxDepth)
                    return fail ("blank nodes nested too deeply");

                ++p;   // '['
                t = newBlank();
                skipSpace();

                if (cur() != ']' && ! predicateObjectList (t))
                    return false;

                if (! expect (']', "at the end of a blank node"))
                    return false;

                --depth;
                return true;
            }

            bool collection (Term& t)
            {
                if (++depth > maxDepth)
                    return fail ("collections nested too deeply");

                ++p;   // '('
                bool empty = true;

                for (;;)
                {
                    skipSpace();

                    if (! more())
                        return fail ("a collection that does not end");

                    if (cur() == ')')
                    {
                        ++p;
                        break;
                    }

                    Term member;
                    if (! object (member))
                        return false;

                    empty = false;
                }

                --depth;

                if (empty)
                {
                    t.kind = Term::Kind::iri;
                    t.value = rdfNil;
                }
                else
                {
                    t = newBlank();
                }

                return true;
            }

            bool stringLiteral (Term& t)
            {
                const auto quote = cur();
                const bool isLong = at (p + 1) == quote && at (p + 2) == quote;
                p += isLong ? 3 : 1;
                std::string text;

                for (;;)
                {
                    if (! more())
                        return fail ("a string that does not end");

                    const auto c = cur();

                    if (c == quote)
                    {
                        if (! isLong)
                        {
                            ++p;
                            break;
                        }

                        if (at (p + 1) == quote && at (p + 2) == quote && at (p + 3) != quote)
                        {
                            p += 3;
                            break;
                        }

                        text += (char) c;
                        ++p;
                        continue;
                    }

                    if (! isLong && (c == '\n' || c == '\r'))
                        return fail ("a line break in a string");

                    if (c == '\\')
                    {
                        const auto e = at (p + 1);

                        if (e == 'u' || e == 'U')
                        {
                            if (! unicodeEscape (text))
                                return false;

                            continue;
                        }

                        static constexpr const char* from = "tbnrf\"'\\";
                        static constexpr const char* to   = "\t\b\n\r\f\"'\\";
                        const char* found = e != 0 ? std::strchr (from, e) : nullptr;

                        if (found == nullptr)
                            return fail ("a bad escape in a string");

                        text += to[found - from];
                        p += 2;
                        continue;
                    }

                    text += (char) c;
                    ++p;
                }

                t.kind = Term::Kind::literal;
                t.value = std::move (text);

                if (cur() == '@')
                {
                    ++p;
                    std::string tag;

                    while (more() && (isAlpha (cur()) || (! tag.empty() && (cur() == '-' || isDigit (cur())))))
                        tag += (char) s[p++];

                    if (tag.empty())
                        return fail ("an empty language tag");

                    t.language = tag;
                }
                else if (cur() == '^' && at (p + 1) == '^')
                {
                    p += 2;
                    return cur() == '<' ? iriRef (t.datatype) : prefixedName (t.datatype);
                }

                return true;
            }

            bool number (Term& t)
            {
                const auto start = p;

                if (cur() == '+' || cur() == '-')
                    ++p;

                size_t digits = 0;
                bool decimal = false, exponent = false;

                for (; isDigit (cur()); ++p)
                    ++digits;

                if (cur() == '.' && isDigit (at (p + 1)))
                {
                    decimal = true;

                    for (++p; isDigit (cur()); ++p)
                        ++digits;
                }

                if (digits > 0 && (cur() == 'e' || cur() == 'E'))
                {
                    exponent = true;
                    ++p;

                    if (cur() == '+' || cur() == '-')
                        ++p;

                    if (! isDigit (cur()))
                        return fail ("a number with an empty exponent");

                    while (isDigit (cur()))
                        ++p;
                }

                if (digits == 0)
                    return fail ("a sign without a number");

                t.kind = Term::Kind::literal;
                t.value = std::string (s.substr (start, p - start));
                t.datatype = std::string (xsd) + (exponent ? "double" : decimal ? "decimal" : "integer");
                return true;
            }

            bool object (Term& t)
            {
                skipSpace();
                const auto c = cur();

                if (c == '<')
                {
                    t.kind = Term::Kind::iri;
                    return iriRef (t.value);
                }

                if (c == '[')                          return blankNodePropertyList (t);
                if (c == '(')                          return collection (t);
                if (c == '_' && at (p + 1) == ':')     return blankLabel (t);
                if (c == '"' || c == '\'')             return stringLiteral (t);

                if (isDigit (c) || c == '+' || c == '-' || (c == '.' && isDigit (at (p + 1))))
                    return number (t);

                for (const auto* word : { "true", "false" })
                {
                    if (keyword (word, false))
                    {
                        t.kind = Term::Kind::literal;
                        t.value = word;
                        t.datatype = std::string (xsd) + "boolean";
                        return true;
                    }
                }

                t.kind = Term::Kind::iri;
                return prefixedName (t.value);
            }

            bool verb (std::string& predicate)
            {
                skipSpace();

                if (keyword ("a", false))
                {
                    predicate = rdfType;
                    return true;
                }

                return cur() == '<' ? iriRef (predicate) : prefixedName (predicate);
            }

            bool objectList (const Term& subject, const std::string& predicate)
            {
                for (;;)
                {
                    Term o;
                    if (! object (o))
                        return false;

                    turtle::Triple t { subject, predicate, std::move (o) };

                    if (! keep || keep (t))
                        out.push_back (std::move (t));

                    skipSpace();

                    if (cur() != ',')
                        return true;

                    ++p;
                }
            }

            bool predicateObjectList (const Term& subject)
            {
                for (;;)
                {
                    std::string predicate;

                    if (! verb (predicate) || ! objectList (subject, predicate))
                        return false;

                    skipSpace();

                    if (cur() != ';')
                        return true;

                    // Any number of ';', and the list may end after them.
                    while (cur() == ';')
                    {
                        ++p;
                        skipSpace();
                    }

                    if (! more() || cur() == '.' || cur() == ']')
                        return true;
                }
            }

            bool triples()
            {
                Term subject;

                if (cur() == '[')
                {
                    if (! blankNodePropertyList (subject))
                        return false;

                    skipSpace();
                    return cur() == '.' || predicateObjectList (subject);
                }

                if (cur() == '<')
                {
                    subject.kind = Term::Kind::iri;
                    if (! iriRef (subject.value))
                        return false;
                }
                else if (cur() == '_' && at (p + 1) == ':')
                {
                    if (! blankLabel (subject))
                        return false;
                }
                else if (cur() == '(')
                {
                    if (! collection (subject))
                        return false;
                }
                else
                {
                    subject.kind = Term::Kind::iri;
                    if (! prefixedName (subject.value))
                        return false;
                }

                return predicateObjectList (subject);
            }
        };
    }

    //==============================================================================
    namespace turtle
    {
        juce::Result parse (std::string_view text, const std::string& baseIri, std::vector<Triple>& out, const Filter& keep)
        {
            return Parser (text, baseIri, out, keep).run();
        }

        std::string fileIri (const juce::File& file)
        {
            std::string iri = "file://";

            for (const auto c : file.getFullPathName().toStdString())
            {
                const auto u = (unsigned char) c;

                if (isAlpha (u) || isDigit (u) || (u != 0 && std::strchr ("/-._~!$&'()*+,;=:@", u) != nullptr))
                {
                    iri += c;
                }
                else
                {
                    static constexpr const char* hex = "0123456789ABCDEF";
                    iri += '%';
                    iri += hex[u >> 4];
                    iri += hex[u & 15];
                }
            }

            return iri;
        }

        juce::File fileOfIri (const std::string& iri)
        {
            if (iri.rfind ("file:", 0) != 0)
                return {};

            auto rest = iri.substr (5);

            if (rest.rfind ("//", 0) == 0)
            {
                const auto slash = rest.find ('/', 2);
                const auto host = rest.substr (2, slash == std::string::npos ? std::string::npos : slash - 2);

                if (slash == std::string::npos || ! (host.empty() || host == "localhost"))
                    return {};

                rest = rest.substr (slash);
            }

            rest = rest.substr (0, rest.find_first_of ("?#"));
            std::string path;

            for (size_t i = 0; i < rest.size(); ++i)
            {
                if (rest[i] == '%' && i + 2 < rest.size() && isHex ((unsigned char) rest[i + 1]) && isHex ((unsigned char) rest[i + 2]))
                {
                    path += (char) (hexValue ((unsigned char) rest[i + 1]) * 16 + hexValue ((unsigned char) rest[i + 2]));
                    i += 2;
                }
                else
                {
                    path += rest[i];
                }
            }

            if (path.empty() || path[0] != '/' || path.find ('\0') != std::string::npos)
                return {};

            return juce::File (juce::String::fromUTF8 (path.data(), (int) path.size()));
        }
    }

    //==============================================================================
    void PluginBundles::loadDirectories (const juce::StringArray& rawDirectories)
    {
        for (const auto& raw : rawDirectories)
        {
            const auto directory = expandLikeLilv (raw.toStdString());

            // lilv_dir_for_each: the order readdir gives, every entry but "." and "..".
            std::vector<std::string> names;

            if (auto* dir = ::opendir (directory.c_str()))
            {
                while (const auto* entry = ::readdir (dir))
                    if (std::strcmp (entry->d_name, ".") != 0 && std::strcmp (entry->d_name, "..") != 0)
                        names.emplace_back (entry->d_name);

                ::closedir (dir);
            }

            for (const auto& name : names)
                loadBundle (fileOfPath (directory + "/" + name));
        }
    }

    void PluginBundles::loadBundle (const juce::File& bundle)
    {
        // ?plugin a lv2:Plugin, in the manifest. A manifest that is missing or does not parse
        // (serd reads strictly) declares nothing.
        std::vector<turtle::Triple> manifest;
        const auto declaresAPlugin = [] (const turtle::Triple& t)
        {
            return t.subject.kind == turtle::Term::Kind::iri && t.predicate == rdfType
                   && t.object.kind == turtle::Term::Kind::iri && t.object.value == lv2Plugin;
        };

        if (readTurtle (bundle.getChildFile ("manifest.ttl"), manifest, declaresAPlugin).failed())
            return;

        std::vector<std::string> declared;
        std::set<std::string> seen;

        for (const auto& t : manifest)
            if (seen.insert (t.subject.value).second)
                declared.push_back (t.subject.value);

        // A plugin another bundle has already: the newer version wins; an older one makes lilv
        // ignore this whole bundle.
        std::vector<std::string> replaced;

        for (const auto& uri : declared)
        {
            const auto known = plugins.find (uri);

            if (known == plugins.end() || known->second == bundle)
                continue;

            const auto order = versionOf (bundle, uri).compare (versionOf (known->second, uri));

            if (order < 0)
                return;

            if (order > 0)
                replaced.push_back (uri);
        }

        // The older bundles go, with every plugin in them (lilv_world_unload_bundle).
        for (const auto& uri : replaced)
        {
            const auto known = plugins.find (uri);

            if (known == plugins.end())
                continue;

            const auto old = known->second;

            for (auto it = plugins.begin(); it != plugins.end();)
                it = it->second == old ? plugins.erase (it) : std::next (it);
        }

        // At the same version, the bundle that came first keeps the plugin.
        for (const auto& uri : declared)
            plugins.emplace (uri, bundle);
    }

    juce::File PluginBundles::bundleOf (const juce::String& uri) const
    {
        const auto found = plugins.find (uri.toStdString());
        return found != plugins.end() ? found->second : juce::File();
    }

    juce::String PluginBundles::describe (const juce::String& uri) const
    {
        const auto bundle = bundleOf (uri);

        if (bundle == juce::File())
            return uri + ", no build identity (bundle not found)";

        const auto identity = BuildIdentity::read (bundle, uri);
        const auto where = ", bundle " + bundle.getFullPathName();

        switch (identity.status)
        {
            case BuildIdentity::Status::identified:
                return uri + ", version " + identity.version + ", source " + identity.sourceId + where;

            case BuildIdentity::Status::unreadable:
                return uri + ", no build identity (buildinfo.ttl unreadable)" + where;

            case BuildIdentity::Status::olderBuild:
                break;
        }

        return uri + ", no build identity (an older build)" + where;
    }

    //==============================================================================
    BuildIdentity BuildIdentity::read (const juce::File& bundle, const juce::String& uri)
    {
        BuildIdentity identity;
        const auto file = bundle.getChildFile ("buildinfo.ttl");

        const auto unreadable = [&identity] (const juce::String& reason)
        {
            identity.status = Status::unreadable;
            identity.reason = reason;
            return identity;
        };

        // No entry at all is a bundle built before the identity. Anything at the path that
        // cannot be read as a small regular file (a dangling link, a directory, a FIFO) is
        // unreadable.
        struct stat st {};
        if (::lstat (file.getFullPathName().toRawUTF8(), &st) != 0)
        {
            if (errno == ENOENT)
                return identity;

            return unreadable (std::strerror (errno));
        }

        std::string text;
        if (auto r = readSmallFile (file, (size_t) maxFileBytes, text); r.failed())
            return unreadable (r.getErrorMessage());

        std::vector<turtle::Triple> triples;
        if (auto r = turtle::parse (text, turtle::fileIri (file), triples); r.failed())
            return unreadable (r.getErrorMessage());

        const auto subject = uri.toStdString();
        const turtle::Term* versionTerm = nullptr;
        const turtle::Term* sourceTerm = nullptr;

        for (const auto& t : triples)
        {
            // Blank nodes may hang off the plugin's statements (a later build may say more);
            // a statement about any other IRI is a file for another plugin.
            if (t.subject.kind != turtle::Term::Kind::iri)
                continue;

            if (t.subject.value != subject)
                return unreadable ("it is about " + juce::String::fromUTF8 (t.subject.value.c_str()) + ", not " + uri);

            for (auto [predicate, slot] : { std::pair { bpiVersion, &versionTerm }, std::pair { bpiSourceId, &sourceTerm } })
            {
                if (t.predicate != predicate)
                    continue;

                if (*slot != nullptr)
                    return unreadable (juce::String (predicate) + " is given twice");

                if (! t.object.isPlainString())
                    return unreadable (juce::String (predicate) + " is not a plain string");

                *slot = &t.object;
            }
        }

        if (versionTerm == nullptr || sourceTerm == nullptr)
            return unreadable (versionTerm == nullptr ? "no bpi:version" : "no bpi:sourceId");

        const auto versionText = juce::String::fromUTF8 (versionTerm->value.c_str());
        const auto sourceText  = juce::String::fromUTF8 (sourceTerm->value.c_str());

        // MAJOR.MINOR.PATCH: three numbers, digits only.
        const auto parts = juce::StringArray::fromTokens (versionText, ".", "");
        const bool versionOk = parts.size() == 3 && ! versionText.endsWith (".")
                               && std::all_of (parts.begin(), parts.end(), [] (const juce::String& part)
                                               { return part.isNotEmpty() && part.containsOnly ("0123456789"); });

        if (! versionOk)
            return unreadable ("the version \"" + versionText + "\" is not MAJOR.MINOR.PATCH");

        // 12 lowercase hex digits, "-dirty" after them when the tree had changes, or "unknown".
        const auto hash = sourceText.upToFirstOccurrenceOf ("-", false, false);
        const auto suffix = sourceText.fromFirstOccurrenceOf ("-", true, false);
        const bool sourceOk = sourceText == "unknown"
                              || (hash.length() == 12 && hash.containsOnly ("0123456789abcdef")
                                  && (suffix.isEmpty() || suffix == "-dirty"));

        if (! sourceOk)
            return unreadable ("the source id \"" + sourceText + "\" is not 12 hex digits, -dirty, or unknown");

        identity.status = Status::identified;
        identity.version = versionText;
        identity.sourceId = sourceText;
        return identity;
    }
}
