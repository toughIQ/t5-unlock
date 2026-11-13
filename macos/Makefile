CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra
LIBUSB_PREFIX ?= $(shell brew --prefix libusb 2>/dev/null || echo /opt/homebrew)
INC = -I$(LIBUSB_PREFIX)/include/libusb-1.0
FRAMEWORKS = -framework IOKit -framework CoreFoundation

STRICT = -O2 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wformat=2 \
         -Wstrict-prototypes -Wmissing-prototypes -Wundef -Werror

t5-unlock-mac: t5-unlock-mac.c
	$(CC) $(CFLAGS) -o $@ $< $(INC) -L$(LIBUSB_PREFIX)/lib -lusb-1.0 $(FRAMEWORKS)

# Self-contained binary (libusb linked in), for copying to another Apple Silicon Mac.
static: t5-unlock-mac.c
	$(CC) $(CFLAGS) -o t5-unlock-mac-static $< $(INC) $(LIBUSB_PREFIX)/lib/libusb-1.0.a $(FRAMEWORKS) -framework Security

selftest: t5-unlock-mac
	./t5-unlock-mac --selftest

# Warnings are errors.
strict: t5-unlock-mac.c
	$(CC) $(STRICT) -o t5-unlock-mac-strict $< $(INC) -L$(LIBUSB_PREFIX)/lib -lusb-1.0 $(FRAMEWORKS)
	./t5-unlock-mac-strict --selftest

# AddressSanitizer and UndefinedBehaviorSanitizer.
sanitize: t5-unlock-mac.c
	$(CC) -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -o t5-unlock-mac-san $< $(INC) -L$(LIBUSB_PREFIX)/lib -lusb-1.0 $(FRAMEWORKS)
	./t5-unlock-mac-san --selftest

# Static analysis (brew install cppcheck).
cppcheck:
	cppcheck --enable=warning,style,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 --quiet t5-unlock-mac.c

# Everything that needs no drive.
check: selftest strict sanitize cppcheck

clean:
	rm -f t5-unlock-mac t5-unlock-mac-static t5-unlock-mac-strict t5-unlock-mac-san
	rm -rf *.dSYM

.PHONY: static selftest strict sanitize cppcheck check clean
