/* A stand-in for speakerd, for the tests of firmware/'s installer and of the unit's
 * speakerd-run (tests/vpair/README.md). It is an ELF program like the real one, because the
 * installer checks that the binary is built for the machine, and it does what it is told:
 *
 *   speakerd --version
 *       prints "speakerd $STUB_VERSION"; without STUB_VERSION it says "bad command line" and
 *       exits 2, as a speakerd from before --version (and before the limits) does
 *   speakerd --import-filtergraph FILE ...
 *       appends its arguments to $STUB_DIR/calls, prints the file $STUB_IMPORT and exits 0;
 *       exits 2 (as speakerd does on a failure) when STUB_IMPORT is unset or FILE does not exist
 *   speakerd anything else
 *       prints "speakerd: stub:" and its arguments, appends them as one line to
 *       $STUB_DIR/calls, then does what the first word of $STUB_DIR/fate says:
 *       segv (dies by SIGSEGV), abrt (SIGABRT), term (SIGTERM), pipe (SIGPIPE), exit N, or
 *       run (waits for a signal; SIGTERM ends it the default way)
 *
 *     cc -O1 -Wall -o speakerd tests/vpair/stub_speakerd.c      (harness.stub_speakerd())
 */
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void record (int argc, char** argv, const char* prefix)
{
    const char* dir = getenv ("STUB_DIR");
    if (dir == NULL)
        return;
    char path[4096];
    snprintf (path, sizeof path, "%s/calls", dir);
    FILE* calls = fopen (path, "a");
    if (calls == NULL)
        return;
    fputs (prefix, calls);
    for (int i = 1; i < argc; ++i)
        fprintf (calls, "%s%s", i > 1 ? " " : "", argv[i]);
    fprintf (calls, "\n");
    fclose (calls);
}

static int import_filtergraph (int argc, char** argv, int at)
{
    record (argc, argv, "import: ");
    const char* out = getenv ("STUB_IMPORT");
    if (out == NULL || at + 1 >= argc || access (argv[at + 1], R_OK) != 0)
    {
        fprintf (stderr, "speakerd: import: FAILED: (stub) no STUB_IMPORT or no %s\n", at + 1 < argc ? argv[at + 1] : "file");
        return 2;
    }
    FILE* f = fopen (out, "r");
    if (f == NULL)
        return 2;
    char buffer[4096];
    size_t n;
    while ((n = fread (buffer, 1, sizeof buffer, f)) > 0)
        fwrite (buffer, 1, n, stdout);
    fclose (f);
    return 0;
}

int main (int argc, char** argv)
{
    for (int i = 1; i < argc; ++i)
    {
        if (strcmp (argv[i], "--import-filtergraph") == 0)
            return import_filtergraph (argc, argv, i);
        if (strcmp (argv[i], "--version") == 0)
        {
            const char* version = getenv ("STUB_VERSION");
            if (version == NULL)
            {
                fprintf (stderr, "speakerd: --version: bad command line\n");
                return 2;
            }
            printf ("speakerd %s\n", version);
            return 0;
        }
    }

    printf ("speakerd: stub:");
    for (int i = 1; i < argc; ++i)
        printf (" %s", argv[i]);
    printf ("\n");
    fflush (stdout);

    char fate[64] = "run";
    record (argc, argv, "");
    const char* dir = getenv ("STUB_DIR");
    if (dir != NULL)
    {
        char path[4096];
        snprintf (path, sizeof path, "%s/fate", dir);
        FILE* f = fopen (path, "r");
        if (f != NULL)
        {
            if (fgets (fate, sizeof fate, f) == NULL)
                strcpy (fate, "run");
            fclose (f);
        }
    }

    if (strncmp (fate, "segv", 4) == 0) raise (SIGSEGV);
    if (strncmp (fate, "abrt", 4) == 0) abort();
    if (strncmp (fate, "term", 4) == 0) raise (SIGTERM);
    if (strncmp (fate, "pipe", 4) == 0) raise (SIGPIPE);
    if (strncmp (fate, "exit", 4) == 0) return atoi (fate + 4);
    for (;;)
        pause();
}
