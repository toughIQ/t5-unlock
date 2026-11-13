CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra
LIBUSB_PREFIX ?= $(shell brew --prefix libusb 2>/dev/null || echo /opt/homebrew)
INC = -I$(LIBUSB_PREFIX)/include/libusb-1.0
FRAMEWORKS = -framework IOKit -framework CoreFoundation

t5-unlock-mac: t5-unlock-mac.c
	$(CC) $(CFLAGS) -o $@ $< $(INC) -L$(LIBUSB_PREFIX)/lib -lusb-1.0 $(FRAMEWORKS)

# Self-contained binary (libusb linked in), for copying to another Apple Silicon Mac.
static: t5-unlock-mac.c
	$(CC) $(CFLAGS) -o t5-unlock-mac-static $< $(INC) $(LIBUSB_PREFIX)/lib/libusb-1.0.a $(FRAMEWORKS) -framework Security

selftest: t5-unlock-mac
	./t5-unlock-mac --selftest

clean:
	rm -f t5-unlock-mac t5-unlock-mac-static

.PHONY: static selftest clean
