# HandAI open bootloader for RG35XX H700

This directory is the migration path away from the binary H700 boot chain.
It uses upstream U-Boot's `anbernic_rg35xx_h700_defconfig` and open
Trusted Firmware-A (`sun50i_h616`) to produce:

- an open eGON SPL that initializes the LPDDR4;
- HandAI-branded mainline U-Boot;
- a signed U-Boot script that loads the existing Linux payload from GPT
  partition 1.

Build only the bootloader:

```sh
bash scripts/build-open-bootloader.sh
```

Create a separate experimental image from an already audited HandAI image:

```sh
bash scripts/build-open-bootloader.sh \
  dist/HandAI-OS-RG35XXSP-full-v2-displayfix.img
```

The experimental image is never the default output. Mainline SPL/U-Boot can be
validated independently while the vendor 4.9 display kernel remains in place.
Promotion requires a successful cold boot and preferably a 3.3 V UART log.
The eGON image is written at the hardware image's proven 256 KiB boot offset,
which keeps the primary GPT entry table intact.
