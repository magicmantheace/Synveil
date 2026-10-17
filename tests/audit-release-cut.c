/* SPDX-License-Identifier: MPL-2.0 */
/* x86_64-only tracing fixture. Never installed in the normal rootfs. */
#define _GNU_SOURCE
#include <errno.h>
#include <linux/audit.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <linux/ptrace.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <unistd.h>

static int remote_name(pid_t child, unsigned long long address, const char *expected) {
    size_t length = strlen(expected);
    for (size_t i = 0; i <= length; ++i) {
        unsigned long location = (unsigned long)address + i;
        errno = 0;
        long word = ptrace(PTRACE_PEEKDATA, child,
                           (void *)(location & ~(sizeof(long) - 1)), NULL);
        if (errno || ((unsigned char *)&word)[location % sizeof(long)] !=
                         (unsigned char)expected[i]) return 0;
    }
    return 1;
}

static int wait_child(pid_t child, int *status) {
    pid_t result;
    do { result = waitpid(child, status, 0); } while (result < 0 && errno == EINTR);
    return result == child;
}

static void checkpoint(const char *phase) {
    printf("\nSYNVEIL_RELEASE_GUEST_CUT=%s\n", phase);
    fflush(stdout);
    /* Keep the tracee at its syscall stop until the HOST kills QEMU. */
    for (;;) pause();
}

int main(int argc, char **argv) {
    const char *phases[] = {"before-unlink", "after-unlink", "unlink-synced", "partial-completion"};
    int phase = -1;
    if (argc != 5) return 2;
    for (int i = 0; i < 4; ++i) if (!strcmp(argv[1], phases[i])) phase = i;
    if (phase < 0 || argv[2][0] != '/' || argv[3][0] != '/' ||
        strlen(argv[3]) > 1024 || strlen(argv[4]) != 128) return 2;
    for (size_t i = 0; i < 128; ++i)
        if (!((argv[4][i] >= '0' && argv[4][i] <= '9') ||
              (argv[4][i] >= 'a' && argv[4][i] <= 'f'))) return 2;
    char active[2048], snapshot[2048], receipt[2048];
    snprintf(active, sizeof(active), "%s/journal", argv[3]);
    snprintf(snapshot, sizeof(snapshot), "%s/copies/archive", argv[3]);
    snprintf(receipt, sizeof(receipt), "%s/copies/release.json", argv[3]);
    pid_t child = fork();
    if (child < 0) return 1;
    if (!child) {
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL)) _exit(125);
        raise(SIGSTOP);
        char *arguments[] = {argv[2], "release-archive", "--path", active,
                             "--snapshot", snapshot, "--authorization", receipt,
                             "--digest", argv[4], NULL};
        execv(argv[2], arguments);
        _exit(126);
    }
    int status, receipt_fd = -1, journal_fd = -1, published = 0, removed = 0, synced = 0;
    int pending_publish = 0, pending_unlink = 0, pending_sync = 0, pending_write = 0;
    int copies_fd = -1, authorization_synced = 0, last_synced_fd = -1, pending_fsync = -1;
    unsigned long options = PTRACE_O_TRACESYSGOOD | PTRACE_O_TRACEEXEC | PTRACE_O_EXITKILL;
    alarm(30); /* A timeout cannot count as a checkpoint. EXITKILL terminates the tracee. */
    if (!wait_child(child, &status)) return 1;
    if (WIFEXITED(status) && WEXITSTATUS(status) == 125) return 77;
    if (!WIFSTOPPED(status)) return 1; /* Already reaped; never signal a stale PID. */
    if (WSTOPSIG(status) != SIGSTOP ||
        ptrace(PTRACE_SETOPTIONS, child, NULL, (void *)options)) goto fail;
    for (unsigned int stops = 0; stops < 100000; ++stops) {
        if (ptrace(PTRACE_SYSCALL, child, NULL, NULL)) goto fail;
        if (!wait_child(child, &status)) return 1;
        if (!WIFSTOPPED(status)) return 1; /* A normal exit is not a cut. */
        if (WSTOPSIG(status) == SIGTRAP && (status >> 16) == PTRACE_EVENT_EXEC) continue;
        if (WSTOPSIG(status) != (SIGTRAP | 0x80)) goto fail;
        struct ptrace_syscall_info info;
        memset(&info, 0, sizeof(info));
        if (ptrace(PTRACE_GET_SYSCALL_INFO, child, sizeof(info), &info) < 0 ||
            info.arch != AUDIT_ARCH_X86_64) goto fail;
        if (info.op == PTRACE_SYSCALL_INFO_ENTRY) {
            pending_publish = pending_unlink = pending_sync = pending_write = 0;
            pending_fsync = info.entry.nr == SYS_fsync ? (int)info.entry.args[0] : -1;
            if (info.entry.nr == SYS_linkat &&
                remote_name(child, info.entry.args[1], "") &&
                remote_name(child, info.entry.args[3], "release.json") &&
                info.entry.args[4] == 0x1000) {
                if (published) goto fail;
                receipt_fd = (int)info.entry.args[0];
                copies_fd = (int)info.entry.args[2];
                if (last_synced_fd != receipt_fd) goto fail;
                pending_publish = 1;
            }
            if (info.entry.nr == SYS_unlinkat &&
                remote_name(child, info.entry.args[1], "journal.1") &&
                info.entry.args[2] == 0) {
                if (!published || !authorization_synced || removed) goto fail;
                journal_fd = (int)info.entry.args[0];
                if (phase == 0) checkpoint(phases[phase]);
                pending_unlink = 1;
            }
            if (removed && info.entry.nr == SYS_fsync &&
                (int)info.entry.args[0] == journal_fd) pending_sync = 1;
            if (synced && info.entry.nr == SYS_write &&
                (int)info.entry.args[0] == receipt_fd && info.entry.args[2] > 0 && phase == 3) {
                struct user_regs_struct regs;
                if (ptrace(PTRACE_GETREGS, child, NULL, &regs)) goto fail;
                regs.rdx = 1; /* Write only the first completion byte. */
                if (ptrace(PTRACE_SETREGS, child, NULL, &regs)) goto fail;
                pending_write = 1;
            }
        } else if (info.op == PTRACE_SYSCALL_INFO_EXIT) {
            if ((pending_publish || pending_unlink || pending_sync) && info.exit.rval != 0) goto fail;
            if (pending_fsync >= 0 && info.exit.rval == 0) {
                last_synced_fd = pending_fsync;
                if (published && pending_fsync == copies_fd) authorization_synced = 1;
            }
            if (pending_publish) published = 1;
            if (pending_unlink) {
                removed = 1;
                if (phase == 1) checkpoint(phases[phase]);
            }
            if (pending_sync) {
                synced = 1;
                if (phase == 2) checkpoint(phases[phase]);
            }
            if (pending_write) {
                if (info.exit.rval != 1) goto fail;
                checkpoint(phases[phase]);
            }
        } else goto fail;
    }
fail:
    fputs("release cut fixture refused or checkpoint missing\n", stderr);
    kill(child, SIGKILL);
    wait_child(child, &status);
    return 1;
}
