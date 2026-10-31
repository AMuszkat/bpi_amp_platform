#pragma once

#include <juce_core/juce_core.h>

#include <functional>
#include <map>
#include <string>
#include <string_view>
#include <vector>

namespace speakerd
{
    /*  Which LV2 bundle a plugin URI runs from, and the build identity of that bundle
        (README.md, "Which build runs").

        JUCE's LV2 hosting finds a plugin by URI in lilv's world, and never says which bundle it
        came from. That world is loaded once, when the LV2 format is made: every bundle in the
        format's default locations (~/.lv2, /usr/lib/lv2, /usr/local/lib/lv2), in that order and,
        in each, in the order the directory lists them (lilv_world_load_all); then each
        --lv2-bundle (lilv_world_load_bundle). JUCE never reads them again. PluginBundles is the
        same map, made at the same moment, from the same directories, by lilv's rules
        (lilv/src/world.c of the pinned JUCE):

        - a bundle declares the plugins its manifest.ttl says are `a lv2:Plugin`; a bundle whose
          manifest is missing or does not parse declares none;
        - a URI that another bundle already declares is decided by lv2:minorVersion and
          lv2:microVersion (read from the manifest and the plugin's rdfs:seeAlso files of each
          bundle; missing is 0.0): a newer one replaces it (the older bundle goes, with every
          plugin in it), an older one makes lilv ignore the whole new bundle, and an equal one
          keeps the first.

        So the bundle named is the one whose plugin runs. Reading every manifest once at start
        is what lilv itself did a moment before; afterwards a lookup reads nothing, and a node's
        buildinfo.ttl is read when its graph is built.

        Message thread only (the main thread of the render). */
    class PluginBundles
    {
    public:
        /** Every bundle in these directories (the LV2 format's default locations, as
            FileSearchPath::getRawString gives them: a leading "~" is $HOME, as lilv expands it). */
        void loadDirectories (const juce::StringArray& rawDirectories);

        /** One bundle, as lilv_world_load_bundle: a --lv2-bundle, or a document's identifier that
            is a bundle's path. Loading a bundle again changes nothing. */
        void loadBundle (const juce::File& bundle);

        /** The bundle the plugin of this URI runs from; File() when no bundle read declares it. */
        juce::File bundleOf (const juce::String& uri) const;

        /** What the log says about a plugin node that runs `uri` (README.md, Log lines), after
            "plugin node <uid>: ": the URI, then its build identity and its bundle, as
            "<URI>, version <v>, source <id>, bundle <dir>", or "<URI>, no build identity (an older
            build), bundle <dir>", "(buildinfo.ttl unreadable), bundle <dir>" or
            "(bundle not found)". Reads the bundle's buildinfo.ttl, each time. */
        juce::String describe (const juce::String& uri) const;

    private:
        std::map<std::string, juce::File> plugins;   // URI -> bundle
    };

    /** A bundle's build identity, its buildinfo.ttl (written by the plugins' build next to
        manifest.ttl):

            @prefix bpi: <https://github.com/AMuszkat/bpi_amp_dsp_tuning#> .

            <PLUGIN-URI>
                bpi:version "MAJOR.MINOR.PATCH" ;
                bpi:sourceId "SOURCE-ID" .

        Read as Turtle (any layout, prefixes or full IRIs), and strictly: it parses, no statement
        is about an IRI other than the bundle's plugin URI, bpi:version and bpi:sourceId of that
        URI each appear once, as plain strings, the version is three numbers ("0.0.2"), and the
        source is 12 lowercase hex digits, optionally followed by "-dirty", or "unknown". Other
        statements about the plugin (and blank nodes under them) are allowed: a later build may
        say more. Anything else is unreadable. */
    struct BuildIdentity
    {
        enum class Status { identified, olderBuild, unreadable };

        Status status = Status::olderBuild;
        juce::String version, sourceId;
        juce::String reason;     // why it is unreadable (not logged: the log line says only that it is)

        /** At most this much of buildinfo.ttl is read; a larger file is unreadable. */
        static constexpr juce::int64 maxFileBytes = 64 * 1024;

        /** No file at all is an older build (one from before the identity). */
        static BuildIdentity read (const juce::File& bundle, const juce::String& uri);
    };

    /** The part of Turtle that LV2 bundles are written in, which is all of its syntax:
        @prefix/@base and PREFIX/BASE, IRIs (relative ones resolved against the base), prefixed
        names, `a`, blank nodes ([ ... ], _:x), collections, string literals (all four quotes,
        escapes, language tags, datatypes), numbers and booleans, comments. Only the statements
        speakerd needs to read are kept as triples: a collection's members are parsed, not kept. */
    namespace turtle
    {
        struct Term
        {
            enum class Kind { iri, blank, literal };

            Kind kind = Kind::iri;
            std::string value;       // the IRI, the blank node's label, or the literal's text
            std::string datatype;    // a literal's datatype IRI; empty for a plain string
            std::string language;    // a literal's language tag

            bool isPlainString() const   { return kind == Kind::literal && datatype.empty() && language.empty(); }
        };

        struct Triple
        {
            Term subject;
            std::string predicate;
            Term object;
        };

        /** Which triples to keep: all of them, unless a reader needs only some. */
        using Filter = std::function<bool (const Triple&)>;

        /** Parses `text`, whose own IRI is `baseIri`, adding its triples (those `keep` takes, when
            given) to `out` as it goes, so that what came before an error is in `out`, as with
            serd. Fails at the first error. */
        juce::Result parse (std::string_view text, const std::string& baseIri, std::vector<Triple>& out,
                            const Filter& keep = {});

        /** "file:///a%20b/manifest.ttl" for "/a b/manifest.ttl", and back (an empty path for an
            IRI that is not a local file's). */
        std::string fileIri (const juce::File&);
        juce::File fileOfIri (const std::string& iri);
    }
}
