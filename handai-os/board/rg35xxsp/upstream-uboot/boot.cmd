# HandAI OS boot script for mainline U-Boot on the RG35XX H700 family.
#
# Partition 1 contains the vendor/BSP Android boot image with the Linux kernel,
# DTB and initramfs. Loading it by GPT metadata keeps this script independent
# of absolute sector offsets.
echo "HandAI OS: loading H700 Linux payload"
part start mmc 0 1 handai_boot_start
part size mmc 0 1 handai_boot_size
mmc read ${kernel_addr_r} ${handai_boot_start} ${handai_boot_size}
bootm ${kernel_addr_r}
