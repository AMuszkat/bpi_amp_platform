#pragma once

#include <juce_core/juce_core.h>

namespace bpi::auth
{
    /** The pair's secret and the proofs made with it (docs/protocol.md, "Authentication").

        The secret is read from a file, never from the command line, and is never logged,
        printed or shown: only proofs leave the controller. */

    /** HMAC-SHA256 (RFC 2104, block size 64) of `message` keyed with `key`, lowercase hex. */
    juce::String hmacSha256Hex (const juce::MemoryBlock& key, const juce::MemoryBlock& message);

    /** The proof of a hello: HMAC-SHA256 keyed with the secret's UTF-8 bytes over the UTF-8
        string "<nonce>:<role>:<name>". */
    juce::String proof (const juce::String& secret, const juce::String& nonce,
                        const juce::String& role, const juce::String& name);

    /** Reads the secret from a file: its whole content without the final line break, 16 to
        256 printable ASCII characters and no spaces. Fails with the reason, never the
        content. Warns (in `warning`) when others than the owner may read the file. */
    juce::Result readSecretFile (const juce::File&, juce::String& secret, juce::String& warning);

    /** The RFC 4231 test cases 1, 2, 3 and 6 (a key longer than a block), as "case N: ok" or
        what differs; false when one fails. */
    bool selfTest (juce::StringArray& report);
}
