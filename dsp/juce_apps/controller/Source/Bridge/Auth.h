#pragma once

#include <juce_core/juce_core.h>

namespace bpi::auth
{
    /** The pair's secret and the proofs made with it (docs/protocol.md, "Authentication").

        The secret is read from a file, never from the command line, and is never logged,
        printed or shown: only proofs leave the controller. */

    /** HMAC-SHA256 (RFC 2104, block size 64) of `message` keyed with `key`, lowercase hex. */
    juce::String hmacSha256Hex (const juce::MemoryBlock& key, const juce::MemoryBlock& message);

    /** HMAC-SHA256 keyed with the secret's UTF-8 bytes over a UTF-8 string, lowercase hex. */
    juce::String hmacHex (const juce::String& key, const juce::String& text);

    /** The client's proof: HMAC("client:<Ns>:<Nc>:<role>:<name>"), with the client's own role
        and name. */
    juce::String clientProof (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce,
                              const juce::String& role, const juce::String& name);

    /** The server's proof: HMAC("server:<Ns>:<Nc>:<role>:<name>"), with the role and name of
        the server's hello ("hub" on the control link, "speaker" on the monitor link). */
    juce::String serverProof (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce,
                              const juce::String& role, const juce::String& name);

    /** The control connection's session key, K = HMAC("session:<Ns>:<Nc>") as hex, and its OSC
        token: the first 32 hex characters of HMAC-SHA256 keyed with K (the UTF-8 bytes of its hex)
        over "osc". */
    juce::String oscToken (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce);

    /** 32 lowercase hex characters from the system's cryptographic source (getrandom, else
        /dev/urandom; arc4random on Apple systems); empty if none answers. */
    juce::String randomNonce();

    /** 32 lowercase hex characters. */
    bool isNonce (const juce::String&);

    /** Two proofs, compared as bytes in a time that does not depend on where they differ. */
    bool sameProof (const juce::String& a, const juce::String& b);

    /** Reads the secret from a file: its whole content without the final line break, 16 to
        256 printable ASCII characters and no spaces. Fails with the reason, never the
        content. Warns (in `warning`) when others than the owner may read the file. */
    juce::Result readSecretFile (const juce::File&, juce::String& secret, juce::String& warning);

    /** The RFC 4231 test cases 1, 2, 3 and 6 (a key longer than a block), as "case N: ok" or
        what differs; false when one fails. */
    bool selfTest (juce::StringArray& report);
}
