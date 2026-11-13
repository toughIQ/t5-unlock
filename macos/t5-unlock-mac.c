/* Unlock a password-protected Samsung Portable SSD T5 on macOS (Apple Silicon).
 *
 * Userspace only: libusb + USB Attached SCSI over USB streams. No kernel
 * extension, no launch daemon, no signing needed. Must run as root (sudo).
 *
 * Protocol (SMART READ/WRITE LOG carried in ATA PASS-THROUGH) as observed by
 * github.com/TurboKach/samsung-t5-ssd-unlock-apple-silicon (MIT) and
 * confirmed on this drive by t5-unlock.py on Linux:
 *   read  0xC8 status    ("PSSD", protocol version 0x00010103)
 *   read  0xD7 challenge
 *   write 0xD6 validate  (password check, no unlock yet)
 *   read  0xD7 challenge (fresh)
 *   write 0xC6 unlock
 *   write 0xCA relink    (drive re-enumerates as the full-capacity disk)
 * payload = SHA256(SHA256(password padded to 32 || chal[0x30:0x40]) || chal[0x00:0x20])
 * placed at offset 0x20 of an otherwise zero 512-byte block.
 *
 * Usage:  sudo t5-unlock-mac --probe     read-only, no password
 *         sudo t5-unlock-mac --unlock    one password attempt, never retries
 *              t5-unlock-mac --selftest  offline checks, no drive, no root
 */
#include <CommonCrypto/CommonDigest.h>
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <errno.h>
#include <fcntl.h>
#include <libusb.h>
#include <readpassphrase.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern char **environ;

#define SAMSUNG_VID 0x04e8
#define T5_LOCKED_PID 0x61f6
#define T5_UNLOCKED_PID 0x61f5
#define SETUP_MAX_BYTES (1ULL << 30)
#define BLOCK 512
#define PASSWORD_MAX 32
#define VERIFIED_VERSION 0x00010103u

#define LOG_STATUS 0xC8
#define LOG_CHALLENGE 0xD7
#define LOG_VALIDATE 0xD6
#define LOG_UNLOCK 0xC6
#define LOG_RELINK 0xCA

#define EP_CMD_OUT 0x04
#define EP_STATUS_IN 0x83
#define EP_DATA_IN 0x81
#define EP_DATA_OUT 0x02

static volatile sig_atomic_t interrupted;
static void on_signal(int sig) { (void)sig; interrupted = 1; }

static void zero(void *p, size_t n) { volatile uint8_t *b = p; while (n--) *b++ = 0; }
static void die(const char *msg, int code) { fprintf(stderr, "%s\n", msg); exit(code); }

/* ---------- pure protocol helpers (covered by --selftest) ---------- */

static int check_password(const uint8_t *pw, size_t len) {
    if (len < 1 || len > PASSWORD_MAX) return 0;
    for (size_t i = 0; i < len; i++) {
        uint8_t c = pw[i];
        if (c >= 0x21 && c <= 0x7e) continue;
        if (c >= 0xd0 && c <= 0xd3 && i + 1 < len && pw[i + 1] >= 0x80 && pw[i + 1] <= 0xbf) { i++; continue; }
        return 0;
    }
    return 1;
}

static int check_challenge(const uint8_t *c) {
    uint8_t any = 0;
    for (int i = 0; i < 32; i++) any |= c[i];
    for (int i = 0x30; i < 0x40; i++) any |= c[i];
    return any != 0;
}

static int make_payload(const uint8_t *pw, size_t len, const uint8_t *challenge, uint8_t out[BLOCK]) {
    memset(out, 0, BLOCK);
    if (!check_password(pw, len) || !check_challenge(challenge)) return 0;
    uint8_t first_in[48] = {0}, second_in[64] = {0};
    memcpy(first_in, pw, len);
    memcpy(first_in + 32, challenge + 0x30, 16);
    CC_SHA256(first_in, sizeof first_in, second_in);
    memcpy(second_in + 32, challenge, 32);
    CC_SHA256(second_in, sizeof second_in, out + 0x20);
    zero(first_in, sizeof first_in);
    zero(second_in, sizeof second_in);
    return 1;
}

static uint32_t status_version(const uint8_t *s) {
    return (uint32_t)s[0x24] | (uint32_t)s[0x25] << 8 | (uint32_t)s[0x26] << 16 | (uint32_t)s[0x27] << 24;
}

/* Returns NULL when the status block matches the verified protocol. */
static const char *check_status(const uint8_t *s) {
    if (memcmp(s + 0x20, "PSSD", 4) != 0) return "status block has no PSSD signature";
    if (status_version(s) != VERIFIED_VERSION) return "protocol version differs from the verified 0x00010103";
    if (s[0x28] & 1) return "drive reports a different hash scheme";
    return NULL;
}

static void ata_cdb(uint8_t log, int write, uint8_t cdb[16]) {
    const uint8_t t[16] = {0x85, write ? 0x0A : 0x08, write ? 0x26 : 0x2E, 0x00, write ? 0xD6 : 0xD5, 0x00, 0x01, 0x00,
                           log, 0x00, 0x4F, 0x00, 0xC2, 0x00, 0xB0, 0x00};
    memcpy(cdb, t, 16);
}

static int ata_sense_ok(const uint8_t *s, int len) {
    if (len < 8 || (s[0] & 0x7f) != 0x72 || (s[1] & 0xf) > 1 || s[2] != 0 || s[3] != 0x1d) return 0;
    int end = 8 + s[7];
    if (end > len) return 0;
    int found = 0;
    for (int i = 8; i < end;) {
        if (end - i < 2) return 0;
        int size = 2 + s[i + 1];
        if (size > end - i) return 0;
        if (s[i] == 9) {
            if (found || size != 14 || s[i + 3] != 0 || (s[i + 13] & 0xa9)) return 0;
            found = 1;
        }
        i += size;
    }
    return found;
}

/* Exact wrong-password reply seen on the verified T5. */
static int validation_rejected(const uint8_t *s, int len) {
    return len == 22 && s[0] == 0x72 && s[1] == 0x0B && s[2] == 0 && s[3] == 0 && s[7] == 14 &&
           s[8] == 9 && s[9] == 12 && s[11] == 4 && s[21] == 0x51;
}

/* ---------- UAS transport over libusb streams ---------- */

typedef struct { struct libusb_transfer *t; int submitted, done, cancelled; } slot_t;
typedef struct {
    int transport_ok;        /* all three transfers completed cleanly */
    uint8_t scsi_status;     /* 0xff when no valid status IU */
    uint8_t sense[64];
    int sense_len;
    int data_len;
} uas_result;

static void LIBUSB_CALL on_done(struct libusb_transfer *t) { ((slot_t *)t->user_data)->done = 1; }

static double now_s(void) {
    struct timespec v;
    clock_gettime(CLOCK_MONOTONIC, &v);
    return (double)v.tv_sec + (double)v.tv_nsec / 1e9;
}

/* One UAS command. write=0: data IN up to want bytes into buf. write=1: send BLOCK bytes from buf. */
static uas_result uas_run(libusb_context *ctx, libusb_device_handle *h, const uint8_t cdb[16], uint16_t tag,
                          int write, uint8_t *buf, int want) {
    uas_result res = {0, 0xff, {0}, 0, 0};
    uint8_t iu[32] = {0}, data[1024] = {0}, st[1024] = {0};
    iu[0] = 1;
    iu[2] = (uint8_t)(tag >> 8);
    iu[3] = (uint8_t)tag;
    memcpy(iu + 16, cdb, 16);
    if (write) memcpy(data, buf, BLOCK);
    slot_t s[3] = {{0}};
    for (int i = 0; i < 3; i++)
        if (!(s[i].t = libusb_alloc_transfer(0))) goto out;
    libusb_fill_bulk_stream_transfer(s[0].t, h, EP_STATUS_IN, tag, st, sizeof st, on_done, &s[0], 5000);
    libusb_fill_bulk_stream_transfer(s[1].t, h, write ? EP_DATA_OUT : EP_DATA_IN, tag, data,
                                     write ? BLOCK : (int)sizeof data, on_done, &s[1], 5000);
    libusb_fill_bulk_transfer(s[2].t, h, EP_CMD_OUT, iu, sizeof iu, on_done, &s[2], 5000);
    int failed = 0;
    double start = now_s();
    for (int i = 0; i < 3; i++) {
        if (interrupted) { failed = 1; break; }
        if (libusb_submit_transfer(s[i].t) != 0) { failed = 1; break; }
        s[i].submitted = 1;
    }
    for (;;) {
        int pending = 0;
        for (int i = 0; i < 3; i++) {
            if (s[i].submitted && !s[i].done) pending++;
            if (s[i].done && s[i].t->status != LIBUSB_TRANSFER_COMPLETED) failed = 1;
        }
        if (!pending) break;
        double t = now_s() - start;
        if (t > 7 || interrupted) failed = 1;
        if (failed)
            for (int i = 0; i < 3; i++)
                if (s[i].submitted && !s[i].done && !s[i].cancelled) { s[i].cancelled = 1; libusb_cancel_transfer(s[i].t); }
        if (t > 12) {
            fputs("USB transfers did not drain. Stopping. Replug the T5. Do not retry a password blindly.\n", stderr);
            _Exit(1);
        }
        struct timeval w = {0, 100000};
        libusb_handle_events_timeout(ctx, &w);
    }
    int sl = s[0].t->actual_length;
    if (s[0].done && s[0].t->status == LIBUSB_TRANSFER_COMPLETED && sl >= 16 && st[0] == 3 &&
        st[2] == (uint8_t)(tag >> 8) && st[3] == (uint8_t)tag) {
        int sn = st[14] << 8 | st[15];
        if (sl - 16 == sn && sn <= (int)sizeof res.sense) {
            res.scsi_status = st[6];
            res.sense_len = sn;
            memcpy(res.sense, st + 16, (size_t)sn);
        }
    }
    res.data_len = s[1].t->actual_length;
    res.transport_ok = !failed && res.scsi_status != 0xff && s[2].done && s[2].t->actual_length == 32 &&
                       res.data_len == (write ? BLOCK : want);
    if (!write && res.transport_ok) memcpy(buf, data, (size_t)want);
out:
    for (int i = 0; i < 3; i++)
        if (s[i].t) libusb_free_transfer(s[i].t);
    zero(data, sizeof data);
    zero(st, sizeof st);
    return res;
}

/* The transport is a function pointer so --selftest can put a fake drive behind it. */
typedef uas_result (*uas_fn)(libusb_context *ctx, libusb_device_handle *h, const uint8_t cdb[16],
                             uint16_t tag, int write, uint8_t *buf, int want);
typedef struct { libusb_context *ctx; libusb_device_handle *h; unsigned seq; uas_fn run; } session_t;

static uint16_t next_tag(session_t *s) { return (uint16_t)(1 + (s->seq++ & 1)); }

static int read_log(session_t *s, uint8_t log, uint8_t block[BLOCK]) {
    uint8_t cdb[16];
    ata_cdb(log, 0, cdb);
    uas_result r = s->run(s->ctx, s->h, cdb, next_tag(s), 0, block, BLOCK);
    if (!r.transport_ok) return 0;
    return r.scsi_status == 0 || (r.scsi_status == 2 && ata_sense_ok(r.sense, r.sense_len));
}

typedef enum { WR_OK, WR_REJECTED, WR_ERROR } write_result;

static write_result write_log(session_t *s, uint8_t log, const uint8_t block[BLOCK]) {
    uint8_t cdb[16], copy[BLOCK];
    ata_cdb(log, 1, cdb);
    memcpy(copy, block, BLOCK);
    uas_result r = s->run(s->ctx, s->h, cdb, next_tag(s), 1, copy, BLOCK);
    zero(copy, sizeof copy);
    if (r.transport_ok && r.scsi_status == 2 && ata_sense_ok(r.sense, r.sense_len)) return WR_OK;
    if (r.transport_ok && r.scsi_status == 2 && validation_rejected(r.sense, r.sense_len)) return WR_REJECTED;
    return WR_ERROR;
}

static int inquiry_is_t5(session_t *s) {
    uint8_t cdb[16] = {0x12, 0, 0, 0, 36, 0}, d[36] = {0};
    uas_result r = s->run(s->ctx, s->h, cdb, next_tag(s), 0, d, 36);
    return r.transport_ok && r.scsi_status == 0 && d[0] == 0 && memcmp(d + 8, "Samsung ", 8) == 0 &&
           memcmp(d + 16, "Portable SSD T5 ", 16) == 0;
}

/* ---------- finding the drive (IOKit) ---------- */

typedef enum { D_ABSENT, D_LOCKED, D_UNLOCKED, D_AMBIGUOUS, D_UNSUPPORTED } dev_state;
typedef struct { dev_state state; char disk[32]; uint64_t size; char serial[256]; } dev_info;

static int str_prop(io_registry_entry_t e, CFStringRef key, char *out, size_t cap) {
    CFTypeRef v = IORegistryEntryCreateCFProperty(e, key, NULL, 0);
    int ok = v && CFGetTypeID(v) == CFStringGetTypeID() && CFStringGetCString(v, out, (CFIndex)cap, kCFStringEncodingUTF8);
    if (v) CFRelease(v);
    return ok;
}

static int num_prop(io_registry_entry_t e, CFStringRef key, uint64_t *out) {
    CFTypeRef v = IORegistryEntryCreateCFProperty(e, key, NULL, 0);
    int64_t n = 0;
    int ok = v && CFGetTypeID(v) == CFNumberGetTypeID() && CFNumberGetValue(v, kCFNumberSInt64Type, &n) && n >= 0;
    if (v) CFRelease(v);
    if (ok) *out = (uint64_t)n;
    return ok;
}

static int is_t5_service(io_service_t svc) {
    CFTypeRef p = IORegistryEntryCreateCFProperty(svc, CFSTR("Device Characteristics"), NULL, 0);
    int ok = 0;
    if (p && CFGetTypeID(p) == CFDictionaryGetTypeID()) {
        CFTypeRef v = CFDictionaryGetValue(p, CFSTR("Vendor Name")), n = CFDictionaryGetValue(p, CFSTR("Product Name"));
        ok = v && n && CFEqual(v, CFSTR("Samsung")) && CFEqual(n, CFSTR("Portable SSD T5"));
    }
    if (p) CFRelease(p);
    return ok;
}

static int usb_identity(io_service_t svc, uint64_t *product, char serial[256]) {
    io_registry_entry_t cur = svc;
    IOObjectRetain(cur);
    while (cur) {
        if (IOObjectConformsTo(cur, "IOUSBHostDevice")) {
            uint64_t vid = 0;
            int ok = num_prop(cur, CFSTR("idVendor"), &vid) && vid == SAMSUNG_VID && num_prop(cur, CFSTR("idProduct"), product) &&
                     str_prop(cur, CFSTR("USB Serial Number"), serial, 256) && serial[0];
            IOObjectRelease(cur);
            return ok;
        }
        io_registry_entry_t parent = IO_OBJECT_NULL;
        IOReturn r = IORegistryEntryGetParentEntry(cur, kIOServicePlane, &parent);
        IOObjectRelease(cur);
        if (r != KERN_SUCCESS) return 0;
        cur = parent;
    }
    return 0;
}

static int whole_media(io_service_t svc, char disk[32], uint64_t *size) {
    io_iterator_t it = IO_OBJECT_NULL;
    if (IORegistryEntryCreateIterator(svc, kIOServicePlane, kIORegistryIterateRecursively, &it) != KERN_SUCCESS) return 0;
    int found = 0;
    io_registry_entry_t c;
    while ((c = IOIteratorNext(it))) {
        if (IOObjectConformsTo(c, "IOMedia")) {
            CFTypeRef w = IORegistryEntryCreateCFProperty(c, CFSTR("Whole"), NULL, 0);
            if (w && CFEqual(w, kCFBooleanTrue) && str_prop(c, CFSTR("BSD Name"), disk, 32) && strncmp(disk, "disk", 4) == 0 &&
                num_prop(c, CFSTR("Size"), size))
                found++;
            if (w) CFRelease(w);
        }
        IOObjectRelease(c);
    }
    IOObjectRelease(it);
    return found == 1;
}

static dev_info find_t5(void) {
    dev_info d = {D_ABSENT, {0}, 0, {0}};
    io_iterator_t it = IO_OBJECT_NULL;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOBlockStorageServices"), &it) != KERN_SUCCESS) {
        d.state = D_UNSUPPORTED;
        return d;
    }
    int matches = 0;
    io_service_t svc;
    while ((svc = IOIteratorNext(it))) {
        if (is_t5_service(svc)) {
            uint64_t product = 0, size = 0;
            char serial[256] = {0}, disk[32] = {0};
            matches++;
            if (!usb_identity(svc, &product, serial) || !whole_media(svc, disk, &size)) d.state = D_UNSUPPORTED;
            else if (product == T5_LOCKED_PID && size < SETUP_MAX_BYTES) d.state = D_LOCKED;
            else if (product == T5_UNLOCKED_PID && size >= SETUP_MAX_BYTES) d.state = D_UNLOCKED;
            else d.state = D_UNSUPPORTED;
            snprintf(d.disk, sizeof d.disk, "%s", disk);
            snprintf(d.serial, sizeof d.serial, "%s", serial);
            d.size = size;
        }
        IOObjectRelease(svc);
    }
    IOObjectRelease(it);
    if (matches > 1) d.state = D_AMBIGUOUS;
    return d;
}

static int run_diskutil_ex(const char *verb, const char *disk, int quiet) {
    pid_t pid;
    char *argv[] = {"diskutil", (char *)verb, (char *)disk, NULL};
    posix_spawn_file_actions_t fa;
    int have_fa = quiet && posix_spawn_file_actions_init(&fa) == 0;
    if (have_fa) {
        posix_spawn_file_actions_addopen(&fa, 1, "/dev/null", O_WRONLY, 0);
        posix_spawn_file_actions_addopen(&fa, 2, "/dev/null", O_WRONLY, 0);
    }
    int spawned = posix_spawn(&pid, "/usr/sbin/diskutil", have_fa ? &fa : NULL, NULL, argv, environ);
    if (have_fa) posix_spawn_file_actions_destroy(&fa);
    if (spawned != 0) return 1;
    int status = 0;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    return !WIFEXITED(status) || WEXITSTATUS(status) != 0;
}

static int run_diskutil(const char *verb, const char *disk) { return run_diskutil_ex(verb, disk, 0); }

/* ---------- USB open/claim ---------- */

static libusb_device *find_usb_locked(libusb_device **list, ssize_t n) {
    libusb_device *t = NULL;
    int matches = 0;
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        if (libusb_get_device_descriptor(list[i], &d) == 0 && d.idVendor == SAMSUNG_VID && d.idProduct == T5_LOCKED_PID) {
            t = list[i];
            matches++;
        }
    }
    return matches == 1 ? t : NULL;
}

typedef struct { libusb_context *ctx; libusb_device **list; libusb_device_handle *h; int claimed, streams; } usb_t;
static unsigned char stream_eps[3] = {EP_STATUS_IN, EP_DATA_IN, EP_DATA_OUT};

static int usb_open(usb_t *u) {
    memset(u, 0, sizeof *u);
    if (libusb_init(&u->ctx) != 0) { fputs("libusb_init failed.\n", stderr); return 0; }
    libusb_set_option(u->ctx, LIBUSB_OPTION_LOG_LEVEL, LIBUSB_LOG_LEVEL_ERROR);
    ssize_t n = libusb_get_device_list(u->ctx, &u->list);
    libusb_device *t = find_usb_locked(u->list, n);
    if (!t) { fputs("Expected exactly one locked T5 on USB (04e8:61f6). Nothing was sent.\n", stderr); return 0; }
    int sp = libusb_get_device_speed(t);
    if (sp != LIBUSB_SPEED_SUPER && sp != LIBUSB_SPEED_SUPER_PLUS) {
        fputs("The T5 is not on a SuperSpeed link (use a USB 3 port and cable). Nothing was sent.\n", stderr);
        return 0;
    }
    int r = libusb_open(t, &u->h);
    if (r != 0) { fprintf(stderr, "Cannot open the T5: %s\n", libusb_error_name(r)); return 0; }
    r = libusb_detach_kernel_driver(u->h, 0);
    if (r != 0 && r != LIBUSB_ERROR_NOT_FOUND) { fprintf(stderr, "Cannot detach the macOS driver: %s\n", libusb_error_name(r)); return 0; }
    r = libusb_claim_interface(u->h, 0);
    if (r != 0) { fprintf(stderr, "Cannot claim the USB interface: %s\n", libusb_error_name(r)); return 0; }
    u->claimed = 1;
    r = libusb_set_interface_alt_setting(u->h, 0, 1);
    if (r != 0) { fprintf(stderr, "Cannot switch the T5 to UAS mode: %s\n", libusb_error_name(r)); return 0; }
    r = libusb_alloc_streams(u->h, 2, stream_eps, 3);
    if (r < 2) { fprintf(stderr, "Cannot allocate USB streams (%d).\n", r); return 0; }
    u->streams = 1;
    return 1;
}

static void usb_close(usb_t *u) {
    if (u->h && u->streams) libusb_free_streams(u->h, stream_eps, 3);
    if (u->h && u->claimed) libusb_release_interface(u->h, 0);
    if (u->h) libusb_close(u->h);
    if (u->list) libusb_free_device_list(u->list, 1);
    if (u->ctx) libusb_exit(u->ctx);
    memset(u, 0, sizeof *u);
}

/* ---------- modes ---------- */

static dev_info require_locked(void) {
    dev_info d = find_t5();
    switch (d.state) {
    case D_LOCKED: return d;
    case D_UNLOCKED:
        printf("The T5 is already unlocked (/dev/%s). Nothing to do.\n", d.disk);
        exit(0);
    case D_ABSENT: die("No Samsung Portable SSD T5 found. Plug it in and wait for the T5_SETUP volume.", 2);
    case D_AMBIGUOUS: die("More than one T5 found. Connect only one. Nothing was sent.", 2);
    default: die("A T5 was found but it is not in the verified locked state. Nothing was sent.", 2);
    }
    return d;
}

static int wait_for_unlocked(const char *serial, dev_info *out) {
    puts("Waiting for the drive to come back as the full-capacity disk ...");
    for (int i = 0; i < 120 && !interrupted; i++) {
        dev_info d = find_t5();
        if (d.state == D_UNLOCKED && strcmp(d.serial, serial) == 0) { *out = d; return 1; }
        struct timespec ts = {0, 500000000};
        nanosleep(&ts, NULL);
    }
    return 0;
}

static int do_probe(void) {
    dev_info d = require_locked();
    printf("Found the locked T5 (setup disk /dev/%s).\n", d.disk);
    printf("Unmounting setup disk %s.\n", d.disk);
    if (run_diskutil("unmountDisk", d.disk) != 0) die("Could not unmount the setup disk. Nothing was sent.", 2);
    usb_t u;
    int rc = 3;
    if (usb_open(&u)) {
        session_t s = {u.ctx, u.h, 0, uas_run};
        uint8_t status[BLOCK] = {0};
        const char *problem = NULL;
        if (!inquiry_is_t5(&s)) puts("INQUIRY did not identify a Samsung Portable SSD T5.");
        else if (!read_log(&s, LOG_STATUS, status)) puts("Could not read the status block.");
        else if ((problem = check_status(status))) printf("Status check failed: %s.\n", problem);
        else {
            puts("Status OK: PSSD, protocol version 0x00010103, hash scheme 0.");
            puts("Probe passed. The drive speaks the verified protocol. No password was sent.");
            rc = 0;
        }
    }
    usb_close(&u);
    puts("The drive may drop off the USB list after a probe. Replug it before unlocking.");
    return rc;
}

/* The whole drive conversation: no prompts, unmounting or waiting. It never loops and never
 * retries. Every command is sent at most once and nothing is sent after the first problem. */
typedef enum {
    UO_UNLOCKED, UO_UNLOCK_ERROR, UO_NOT_T5, UO_NO_STATUS, UO_BAD_STATUS, UO_NO_CHALLENGE,
    UO_REJECTED, UO_VALIDATION_UNCLEAR, UO_INTERRUPTED, UO_NO_FRESH_CHALLENGE
} unlock_outcome;

static unlock_outcome unlock_sequence(session_t *s, const uint8_t *pw, size_t len, const char **detail,
                                      int *relink_clean) {
    uint8_t block[BLOCK] = {0}, challenge[BLOCK] = {0};
    unlock_outcome out;
    write_result w;
    *detail = "";
    *relink_clean = 1;
    if (!inquiry_is_t5(s)) { out = UO_NOT_T5; goto end; }
    if (!read_log(s, LOG_STATUS, block)) { out = UO_NO_STATUS; goto end; }
    if ((*detail = check_status(block)) != NULL) { out = UO_BAD_STATUS; goto end; }
    if (!read_log(s, LOG_CHALLENGE, challenge) || !make_payload(pw, len, challenge, block)) { out = UO_NO_CHALLENGE; goto end; }
    w = write_log(s, LOG_VALIDATE, block);
    if (w == WR_REJECTED) { out = UO_REJECTED; goto end; }
    if (w != WR_OK) { out = UO_VALIDATION_UNCLEAR; goto end; }
    if (interrupted) { out = UO_INTERRUPTED; goto end; }
    if (!read_log(s, LOG_CHALLENGE, challenge) || !make_payload(pw, len, challenge, block)) { out = UO_NO_FRESH_CHALLENGE; goto end; }
    if (write_log(s, LOG_UNLOCK, block) != WR_OK) { out = UO_UNLOCK_ERROR; goto end; }
    memset(block, 0, BLOCK);
    *relink_clean = write_log(s, LOG_RELINK, block) == WR_OK;
    out = UO_UNLOCKED;
end:
    zero(block, sizeof block);
    zero(challenge, sizeof challenge);
    return out;
}

static int do_unlock(void) {
    dev_info d = require_locked();
    printf("Found the locked T5 (setup disk /dev/%s).\n", d.disk);
    uint8_t pw[PASSWORD_MAX + 2] = {0};
    char buf1[128], buf2[128];
    if (!readpassphrase("T5 DRIVE password (not your Mac password): ", buf1, sizeof buf1, RPP_REQUIRE_TTY) ||
        !readpassphrase("Repeat the T5 DRIVE password: ", buf2, sizeof buf2, RPP_REQUIRE_TTY))
        die("Could not read the password from the terminal. Nothing was sent.", 2);
    size_t len = strlen(buf1);
    int same = strcmp(buf1, buf2) == 0;
    zero(buf2, sizeof buf2);
    if (!same) { zero(buf1, sizeof buf1); die("Passwords do not match. Nothing was sent.", 2); }
    if (len > PASSWORD_MAX || !check_password((const uint8_t *)buf1, len)) {
        zero(buf1, sizeof buf1);
        die("Password must be 1-32 bytes of printable ASCII (no spaces) or Cyrillic. Nothing was sent.", 2);
    }
    memcpy(pw, buf1, len);
    zero(buf1, sizeof buf1);

    printf("Unmounting setup disk %s.\n", d.disk);
    if (run_diskutil("unmountDisk", d.disk) != 0) { zero(pw, sizeof pw); die("Could not unmount the setup disk. Nothing was sent.", 2); }

    usb_t u;
    int rc = 3;
    int sent_unlock = 0;
    if (usb_open(&u)) {
        session_t s = {u.ctx, u.h, 0, uas_run};
        const char *detail = "";
        int relink_clean = 1;
        unlock_outcome o = unlock_sequence(&s, pw, len, &detail, &relink_clean);
        if (o == UO_UNLOCKED || o == UO_UNLOCK_ERROR || o == UO_INTERRUPTED || o == UO_NO_FRESH_CHALLENGE)
            puts("Password accepted by the drive.");
        switch (o) {
        case UO_NOT_T5: fputs("INQUIRY did not identify a Samsung Portable SSD T5. No password was sent.\n", stderr); break;
        case UO_NO_STATUS: fputs("Could not read the drive status. No password was sent.\n", stderr); break;
        case UO_BAD_STATUS: fprintf(stderr, "Status check failed: %s. No password was sent.\n", detail); break;
        case UO_NO_CHALLENGE: fputs("Could not get a usable challenge. No password was sent.\n", stderr); break;
        case UO_REJECTED: fputs("Wrong password: the drive rejected it. No unlock command was sent.\n", stderr); rc = 4; break;
        case UO_VALIDATION_UNCLEAR:
            fputs("Password validation failed at transport level (outcome unclear). No unlock command was sent. Do not retry blindly.\n", stderr);
            break;
        case UO_INTERRUPTED: fputs("Interrupted before the unlock command. Nothing unlocked.\n", stderr); break;
        case UO_NO_FRESH_CHALLENGE: fputs("Could not read a fresh challenge. No unlock command was sent.\n", stderr); break;
        case UO_UNLOCK_ERROR:
            fputs("Unlock returned an error; checking for the data disk anyway, no relink sent.\n", stderr);
            sent_unlock = 1;
            rc = 0;
            break;
        case UO_UNLOCKED:
            puts("Unlock sent, then relink sent (a disconnect here is expected).");
            if (!relink_clean) puts("Relink gave no clean reply (normal while the drive re-enumerates).");
            sent_unlock = 1;
            rc = 0;
            break;
        }
    }
    zero(pw, sizeof pw);
    usb_close(&u);
    if (!sent_unlock) return rc;

    dev_info after;
    if (!wait_for_unlocked(d.serial, &after)) {
        fputs("The unlocked disk did not show up within 60 s. Replug the T5 and check Disk Utility; it stays unlocked only while powered.\n", stderr);
        return 5;
    }
    printf("Unlocked T5 appeared: /dev/%s, %.1f GiB.\n", after.disk, (double)after.size / (1024.0 * 1024.0 * 1024.0));
    /* macOS often mounts the disk by itself at the same moment, so a first failure is not a failure.
     * Ask a few times, quietly, and only complain if it is still not mounted. */
    int mounted = 0;
    for (int i = 0; i < 6 && !mounted && !interrupted; i++) {
        mounted = run_diskutil_ex("mountDisk", after.disk, 1) == 0;
        if (!mounted) {
            struct timespec ts = {2, 0};
            nanosleep(&ts, NULL);
        }
    }
    if (mounted) puts("The volume is mounted.");
    else puts("The drive is unlocked but did not mount yet. If macOS asks to allow the accessory, click Allow, then it will mount.");
    return 0;
}

/* ---------- offline self test ---------- */

static int fails;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); fails++; } }

static void hex32(const uint8_t *p, char out[65]) { for (int i = 0; i < 32; i++) snprintf(out + 2 * i, 3, "%02x", p[i]); }

/* ---------- fake drive for the safety-rule tests ---------- */

enum { FK_OK, FK_REJECT, FK_GARBAGE, FK_TRANSPORT };
enum { C_INQUIRY = 0x100, C_READ = 0x200, C_WRITE = 0x300 };

static struct {
    int validate, unlock, relink;            /* F_* behaviour per write */
    int bad_version, empty_challenge, wrong_inquiry;
    int cmds[32], ncmds;                     /* C_INQUIRY, C_READ|log, C_WRITE|log */
    int leaked, malformed;                   /* password in clear / payload outside 0x20..0x3f */
    const uint8_t *pw;
    size_t pwlen;
} fake;

static void fake_reset(const uint8_t *pw, size_t pwlen) {
    memset(&fake, 0, sizeof fake);
    fake.pw = pw;
    fake.pwlen = pwlen;
}

static uas_result fake_run(libusb_context *ctx, libusb_device_handle *h, const uint8_t cdb[16], uint16_t tag,
                           int write, uint8_t *buf, int want) {
    static const uint8_t ok_sense[22] = {0x72, 0x01, 0x00, 0x1D, 0, 0, 0, 14, 0x09, 0x0C, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x50};
    static const uint8_t rej_sense[22] = {0x72, 0x0B, 0x00, 0x00, 0, 0, 0, 14, 0x09, 0x0C, 0, 0x04, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x51};
    static const uint8_t bad_sense[22] = {0x70, 0x05, 0, 0, 0, 0, 0, 10};
    uas_result r = {0, 0xff, {0}, 0, 0};
    (void)ctx; (void)h; (void)tag; (void)write;
    if (fake.ncmds >= 32) { fake.malformed = 1; return r; }
    if (cdb[0] == 0x12) {
        fake.cmds[fake.ncmds++] = C_INQUIRY;
        memset(buf, 0, (size_t)want);
        memcpy(buf + 8, fake.wrong_inquiry ? "Other   Vendor Disk    " : "Samsung Portable SSD T5 ", 24);
        r.transport_ok = 1;
        r.scsi_status = 0;
        r.data_len = want;
        return r;
    }
    uint8_t log = cdb[8];
    if (cdb[1] != 0x0A) {
        fake.cmds[fake.ncmds++] = C_READ | log;
        memset(buf, 0, BLOCK);
        if (log == LOG_STATUS) {
            memcpy(buf + 0x20, "PSSD", 4);
            buf[0x24] = fake.bad_version ? 0x00 : 0x03;
            buf[0x25] = fake.bad_version ? 0x00 : 0x01;
            buf[0x26] = fake.bad_version ? 0x02 : 0x01;
        } else if (!fake.empty_challenge) {
            for (int i = 0; i < BLOCK; i++) buf[i] = (uint8_t)((i * 7 + 3) % 256);
        }
        r.transport_ok = 1;
        r.scsi_status = 0;
        r.data_len = BLOCK;
        return r;
    }
    fake.cmds[fake.ncmds++] = C_WRITE | log;
    if (fake.pw && fake.pwlen && memmem(buf, BLOCK, fake.pw, fake.pwlen)) fake.leaked = 1;
    for (int i = 0; i < BLOCK; i++)
        if (buf[i] && (log == LOG_RELINK || i < 0x20 || i >= 0x40)) fake.malformed = 1;
    int how = log == LOG_VALIDATE ? fake.validate : log == LOG_UNLOCK ? fake.unlock : fake.relink;
    r.data_len = BLOCK;
    if (how == FK_TRANSPORT) return r;
    r.transport_ok = 1;
    r.scsi_status = 2;
    r.sense_len = 22;
    memcpy(r.sense, how == FK_OK ? ok_sense : how == FK_REJECT ? rej_sense : bad_sense, 22);
    return r;
}

static unlock_outcome run_fake(const uint8_t *pw, size_t pwlen) {
    session_t s = {NULL, NULL, 0, fake_run};
    const char *detail = "";
    int relink_clean = 1;
    return unlock_sequence(&s, pw, pwlen, &detail, &relink_clean);
}

static int fake_count(int cmd) {
    int n = 0;
    for (int i = 0; i < fake.ncmds; i++) n += fake.cmds[i] == cmd;
    return n;
}

static int fake_writes(void) {
    int n = 0;
    for (int i = 0; i < fake.ncmds; i++) n += (fake.cmds[i] & 0xF00) == C_WRITE;
    return n;
}

static void safety_tests(void) {
    const uint8_t *pw = (const uint8_t *)"Correct-Horse_42";
    const size_t pl = 16;
    unlock_outcome o;

    fake_reset(pw, pl);
    o = run_fake(pw, pl);
    const int want[] = {C_INQUIRY, C_READ | LOG_STATUS, C_READ | LOG_CHALLENGE, C_WRITE | LOG_VALIDATE,
                        C_READ | LOG_CHALLENGE, C_WRITE | LOG_UNLOCK, C_WRITE | LOG_RELINK};
    check(o == UO_UNLOCKED, "mock: happy path unlocks");
    check(fake.ncmds == 7 && memcmp(fake.cmds, want, sizeof want) == 0, "mock: exact command sequence, each sent once");
    check(!fake.leaked && !fake.malformed, "mock: password never in clear, payload only at 0x20..0x3f");

    fake_reset(pw, pl);
    fake.validate = FK_REJECT;
    o = run_fake(pw, pl);
    check(o == UO_REJECTED, "mock: rejected password reported");
    check(fake_writes() == 1 && fake_count(C_WRITE | LOG_VALIDATE) == 1, "mock: rejection sends nothing after validate, no retry");

    const int modes[] = {FK_GARBAGE, FK_TRANSPORT};
    for (size_t i = 0; i < 2; i++) {
        fake_reset(pw, pl);
        fake.validate = modes[i];
        o = run_fake(pw, pl);
        check(o == UO_VALIDATION_UNCLEAR, "mock: unclear validation stops");
        check(fake_writes() == 1 && fake_count(C_WRITE | LOG_VALIDATE) == 1, "mock: unclear validation, one attempt, no unlock");
    }

    fake_reset(pw, pl);
    fake.unlock = FK_GARBAGE;
    o = run_fake(pw, pl);
    check(o == UO_UNLOCK_ERROR, "mock: unlock error reported");
    check(fake_count(C_WRITE | LOG_UNLOCK) == 1 && fake_count(C_WRITE | LOG_RELINK) == 0, "mock: unlock error sends no relink, no retry");

    fake_reset(pw, pl);
    fake.relink = FK_TRANSPORT;
    o = run_fake(pw, pl);
    check(o == UO_UNLOCKED && fake_count(C_WRITE | LOG_RELINK) == 1, "mock: failed relink not fatal, not retried");

    fake_reset(pw, pl);
    fake.wrong_inquiry = 1;
    o = run_fake(pw, pl);
    check(o == UO_NOT_T5 && fake.ncmds == 1, "mock: wrong drive gets only an inquiry");

    fake_reset(pw, pl);
    fake.bad_version = 1;
    o = run_fake(pw, pl);
    check(o == UO_BAD_STATUS && fake_writes() == 0 && fake_count(C_READ | LOG_CHALLENGE) == 0,
          "mock: unverified protocol version, nothing password related sent");

    fake_reset(pw, pl);
    fake.empty_challenge = 1;
    o = run_fake(pw, pl);
    check(o == UO_NO_CHALLENGE && fake_writes() == 0, "mock: empty challenge sends no writes");

    fake_reset(pw, pl);
    fake.validate = FK_TRANSPORT;
    run_fake(pw, pl);
    check(fake.ncmds <= 7 && !fake.malformed, "mock: command count is bounded");
}

static int do_selftest(void) {
    uint8_t ch[BLOCK], chr[BLOCK], out[BLOCK];
    char hex[65];
    for (int i = 0; i < BLOCK; i++) { ch[i] = (uint8_t)(i % 256); chr[i] = (uint8_t)(255 - (i % 256)); }
    struct { const char *pw; const char *hex; } v[] = {
        {"password", "0300faba3e3fb4124e3a8bd285a8c63bb84bb232276a9ae9984a325009ca381b"},
        {"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "026e0a13661efa6527e9ac9281d7e0135c4f6178e5f6949ea55590e653d7e30f"},
        {"!", "a31b551259b9c81c5e81281fb26138f92bcd660f5b77d2e3ed1a11bcef6c64ed"},
        {"Correct-Horse_42", "f8fff8c890e90098cc70cb563e44c258e477d2ee35f28b47e39793b2bbb43ebe"},
        {"\xd0\xa2\xd0\xb5\xd1\x81\xd1\x82-Only42", "18e24d41263f1f54d9022f0aaf125adc0037a657b1392fb55451a8b8270bf35e"},
        {"\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf\xd0\xaf",
         "5141c096f8bc046ed8a6137b0eec47ba8c3b3d2b6db28de50c58887f282969a0"},
    };
    for (size_t i = 0; i < sizeof v / sizeof v[0]; i++) {
        size_t l = strlen(v[i].pw);
        check(check_password((const uint8_t *)v[i].pw, l), "password accepted");
        check(make_payload((const uint8_t *)v[i].pw, l, ch, out), "payload built");
        hex32(out + 0x20, hex);
        check(strcmp(hex, v[i].hex) == 0, "hash vector");
        int clean = 1;
        for (int k = 0; k < 0x20; k++) clean &= out[k] == 0;
        for (int k = 0x40; k < BLOCK; k++) clean &= out[k] == 0;
        check(clean, "payload only at 0x20..0x3f");
    }
    make_payload((const uint8_t *)"password", 8, chr, out);
    hex32(out + 0x20, hex);
    check(strcmp(hex, "f835d737a17cc8547c4110f5b5488616ea5d6da6b085ab5ea8428a078b7429db") == 0, "reversed challenge vector");

    uint8_t c2[BLOCK];
    memcpy(c2, ch, BLOCK);
    for (int i = 0x20; i < 0x30; i++) c2[i] ^= 0xff;
    for (int i = 0x40; i < BLOCK; i++) c2[i] ^= 0xff;
    make_payload((const uint8_t *)"password", 8, c2, out);
    hex32(out + 0x20, hex);
    check(strcmp(hex, v[0].hex) == 0, "bytes outside the challenge fields are ignored");
    memset(c2, 0, BLOCK);
    c2[0x20] = 1;
    check(!make_payload((const uint8_t *)"password", 8, c2, out), "empty challenge refused");

    check(!check_password((const uint8_t *)"", 0), "empty password refused");
    check(!check_password((const uint8_t *)"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 33), "33 bytes refused");
    check(!check_password((const uint8_t *)"with space", 10), "space refused");
    check(!check_password((const uint8_t *)"tab\t", 4), "tab refused");
    check(!check_password((const uint8_t *)"\xc3\x84", 2), "umlaut refused");

    uint8_t st[BLOCK] = {0};
    memcpy(st + 0x20, "PSSD", 4);
    st[0x24] = 0x03; st[0x25] = 0x01; st[0x26] = 0x01;
    check(check_status(st) == NULL, "status ok");
    st[0x28] = 1;
    check(check_status(st) != NULL, "hash scheme flag refused");
    st[0x28] = 0; st[0x24] = 4;
    check(check_status(st) != NULL, "other version refused");

    uint8_t cdb[16];
    const uint8_t want_r[16] = {0x85, 0x08, 0x2e, 0x00, 0xd5, 0x00, 0x01, 0x00, 0xc8, 0x00, 0x4f, 0x00, 0xc2, 0x00, 0xb0, 0x00};
    const uint8_t want_w[16] = {0x85, 0x0a, 0x26, 0x00, 0xd6, 0x00, 0x01, 0x00, 0xd6, 0x00, 0x4f, 0x00, 0xc2, 0x00, 0xb0, 0x00};
    ata_cdb(0xC8, 0, cdb);
    check(memcmp(cdb, want_r, 16) == 0, "read CDB bytes");
    ata_cdb(0xD6, 1, cdb);
    check(memcmp(cdb, want_w, 16) == 0, "write CDB bytes");

    uint8_t ok_sense[22] = {0x72, 0x01, 0x00, 0x1D, 0, 0, 0, 14, 0x09, 0x0C, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x50};
    const uint8_t rej[22] = {0x72, 0x0B, 0x00, 0x00, 0, 0, 0, 14, 0x09, 0x0C, 0, 0x04, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x51};
    check(ata_sense_ok(ok_sense, 22), "ATA success sense");
    check(validation_rejected(rej, 22), "wrong-password sense");
    check(!ata_sense_ok(rej, 22), "rejection is not success");
    check(!validation_rejected(ok_sense, 22), "success is not rejection");
    ok_sense[21] = 0x51;
    check(!ata_sense_ok(ok_sense, 22), "ATA error bit refused");

    safety_tests();

    if (fails) { printf("%d check(s) failed.\n", fails); return 1; }
    puts("PASS: hash vectors, challenge fields, password guards, status, CDBs, sense parsing, mock-drive safety rules");
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) die("Usage: sudo t5-unlock-mac --probe | --unlock   (offline: t5-unlock-mac --selftest)", 64);
    if (strcmp(argv[1], "--selftest") == 0) return do_selftest();
    int probe = strcmp(argv[1], "--probe") == 0, unlock = strcmp(argv[1], "--unlock") == 0;
    if (!probe && !unlock) die("Usage: sudo t5-unlock-mac --probe | --unlock   (offline: t5-unlock-mac --selftest)", 64);
    if (geteuid() != 0) die("Run as root: sudo t5-unlock-mac --unlock", 77);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    return probe ? do_probe() : do_unlock();
}
