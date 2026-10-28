#include "Auth.h"

#include <juce_cryptography/juce_cryptography.h>

#if ! JUCE_WINDOWS
 #include <sys/stat.h>
 #include <fcntl.h>
 #include <unistd.h>
#endif

#if JUCE_LINUX
 #include <sys/random.h>
#endif

#if JUCE_MAC || JUCE_IOS
 #include <stdlib.h>
#endif

namespace bpi::auth
{
    namespace
    {
        constexpr size_t blockSize = 64;   // SHA-256's block, RFC 2104's B

        juce::MemoryBlock sha256 (const juce::MemoryBlock& a, const juce::MemoryBlock& b)
        {
            juce::MemoryBlock joined (a);
            joined.append (b.getData(), b.getSize());
            return juce::SHA256 (joined).getRawData();
        }

        juce::MemoryBlock utf8 (const juce::String& s)
        {
            return { s.toRawUTF8(), s.getNumBytesAsUTF8() };
        }

        juce::MemoryBlock fromHex (const juce::String& hex)
        {
            juce::MemoryBlock m;
            m.loadFromHexString (hex);
            return m;
        }
    }

    juce::String hmacSha256Hex (const juce::MemoryBlock& keyIn, const juce::MemoryBlock& message)
    {
        // K: a key longer than a block is hashed first, then zero-padded to the block.
        auto key = keyIn.getSize() > blockSize ? juce::SHA256 (keyIn).getRawData() : keyIn;
        key.ensureSize (blockSize, true);

        juce::MemoryBlock inner (blockSize), outer (blockSize);

        for (size_t i = 0; i < blockSize; ++i)
        {
            inner[i] = (char) (key[i] ^ 0x36);
            outer[i] = (char) (key[i] ^ 0x5c);
        }

        // H((K ^ opad) || H((K ^ ipad) || message))
        const auto mac = sha256 (outer, sha256 (inner, message));
        return juce::String::toHexString (mac.getData(), (int) mac.getSize(), 0).toLowerCase();
    }

    juce::String hmacHex (const juce::String& key, const juce::String& text)
    {
        return hmacSha256Hex (utf8 (key), utf8 (text));
    }

    juce::String clientProof (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce,
                              const juce::String& role, const juce::String& name)
    {
        return hmacHex (secret, "client:" + serverNonce + ":" + clientNonce + ":" + role + ":" + name);
    }

    juce::String serverProof (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce,
                              const juce::String& role, const juce::String& name)
    {
        return hmacHex (secret, "server:" + serverNonce + ":" + clientNonce + ":" + role + ":" + name);
    }

    juce::String sessionKey (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce)
    {
        return hmacHex (secret, "session:" + serverNonce + ":" + clientNonce);
    }

    juce::String oscToken (const juce::String& secret, const juce::String& serverNonce, const juce::String& clientNonce)
    {
        return hmacHex (sessionKey (secret, serverNonce, clientNonce), "osc").substring (0, 32);
    }

    juce::String randomNonce()
    {
        unsigned char bytes[16] {};
        bool ok = false;

       #if JUCE_LINUX
        ok = getrandom (bytes, sizeof (bytes), 0) == (ssize_t) sizeof (bytes);
       #elif JUCE_MAC || JUCE_IOS
        arc4random_buf (bytes, sizeof (bytes));
        ok = true;
       #endif

       #if ! JUCE_WINDOWS
        if (! ok)
        {
            const auto fd = open ("/dev/urandom", O_RDONLY | O_CLOEXEC);

            if (fd >= 0)
            {
                ok = read (fd, bytes, sizeof (bytes)) == (ssize_t) sizeof (bytes);
                close (fd);
            }
        }
       #endif

        if (! ok)
            return {};   // never a guessable nonce: no handshake instead

        return juce::String::toHexString (bytes, (int) sizeof (bytes), 0).toLowerCase();
    }

    bool isNonce (const juce::String& s)
    {
        return s.length() == 32 && s.containsOnly ("0123456789abcdef");
    }

    bool sameProof (const juce::String& a, const juce::String& b)
    {
        const auto* x = a.toRawUTF8();
        const auto* y = b.toRawUTF8();
        const auto lx = a.getNumBytesAsUTF8(), ly = b.getNumBytesAsUTF8();

        // Every byte of the expected length is looked at, whatever differs where.
        unsigned difference = (unsigned) (lx ^ ly);

        for (size_t i = 0; i < ly; ++i)
            difference |= (unsigned) ((unsigned char) (i < lx ? x[i] : 0) ^ (unsigned char) y[i]);

        return difference == 0 && ly > 0;
    }

    juce::Result readSecretFile (const juce::File& file, juce::String& secret, juce::String& warning)
    {
        secret.clear();
        warning.clear();

        if (! file.existsAsFile())
            return juce::Result::fail ("no secret file " + file.getFullPathName());

        if (file.getSize() > 4096)
            return juce::Result::fail ("the secret file " + file.getFullPathName() + " is too large to be a secret");

        auto text = file.loadFileAsString();

        if (text.endsWithChar ('\n')) text = text.dropLastCharacters (1);
        if (text.endsWithChar ('\r')) text = text.dropLastCharacters (1);

        const auto length = text.length();
        bool printable = true;

        for (auto c : text)
            if (c <= 0x20 || c >= 0x7f)
                printable = false;

        if (length < 16 || length > 256 || ! printable)
            return juce::Result::fail ("the secret in " + file.getFullPathName()
                                       + " must be 16 to 256 printable characters without spaces");

       #if ! JUCE_WINDOWS
        struct stat st {};
        if (stat (file.getFullPathName().toRawUTF8(), &st) == 0 && (st.st_mode & (S_IRWXG | S_IRWXO)) != 0)
            warning = "the secret file " + file.getFullPathName() + " can be read by others: chmod 600 it";
       #endif

        secret = text;
        return juce::Result::ok();
    }

    bool selfTest (juce::StringArray& report)
    {
        struct Case { int number; juce::MemoryBlock key, data; const char* expected; };

        juce::MemoryBlock key3, data3, key6;
        key3.setSize (20); key3.fillWith (0xaa);
        data3.setSize (50); data3.fillWith (0xdd);
        key6.setSize (131); key6.fillWith (0xaa);

        const Case cases[] =
        {
            { 1, fromHex ("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b"), utf8 ("Hi There"),
              "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7" },
            { 2, utf8 ("Jefe"), utf8 ("what do ya want for nothing?"),
              "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843" },
            { 3, key3, data3,
              "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe" },
            { 6, key6, utf8 ("Test Using Larger Than Block-Size Key - Hash Key First"),
              "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54" },
        };

        bool ok = true;

        for (const auto& c : cases)
        {
            const auto got = hmacSha256Hex (c.key, c.data);
            const auto pass = got == c.expected;
            ok = ok && pass;
            report.add ("RFC 4231 case " + juce::String (c.number) + ": " + (pass ? juce::String ("ok") : "got " + got + ", want " + c.expected));
        }

        // The protocol's known-answer vector (computed with Python's hmac and hashlib).
        const juce::String secret ("0123456789abcdef-pair-secret");
        const juce::String ns ("00112233445566778899aabbccddeeff"), nc ("ffeeddccbbaa99887766554433221100");

        const std::pair<const char*, juce::String> vector[] =
        {
            { "client_proof", clientProof (secret, ns, nc, "controller", "ctl-test") },
            { "server_proof", serverProof (secret, ns, nc, "hub", "left") },
            { "session_key",  sessionKey (secret, ns, nc) },
            { "osc_token",    oscToken (secret, ns, nc) },
        };

        const char* const expected[] =
        {
            "0e82cbf2d55bbe8c34cae30db176a88e6a3f9227a6322140df7f2a3142e8e22a",
            "e067654372eb4ce9d2d86af15cf1fcaa9a311476d00e0b4645961852884cffb4",
            "9bd062cf4dbaf55e3e6efb6e07e908ce9bccc3ccbb271158ee5a48cb867d638a",
            "390b2272219a58e77b6ad16b88cd17ac",
        };

        for (size_t i = 0; i < 4; ++i)
        {
            const auto pass = vector[i].second == expected[i];
            ok = ok && pass;
            report.add ("vector " + juce::String (vector[i].first) + ": " + vector[i].second + (pass ? " ok" : juce::String (" want ") + expected[i]));
        }

        return ok;
    }
}
