################################################################################
#
# handai
#
# Installs the HandAI Python package (stdlib-only, no pip deps) to /opt/handai.
# Source is the parent repo of this external tree (local site method), so a
# checkout of the HandAI repo with handai-os/ inside it builds as-is.
#
################################################################################

HANDAI_VERSION = 0.1.0
HANDAI_SITE = $(BR2_EXTERNAL_HANDAI_PATH)/..
HANDAI_SITE_METHOD = local
HANDAI_LICENSE = PolyForm-Noncommercial-1.0.0
HANDAI_LICENSE_FILES = LICENSE

define HANDAI_INSTALL_TARGET_CMDS
	$(INSTALL) -d $(TARGET_DIR)/opt/handai
	rm -rf $(TARGET_DIR)/opt/handai/handai
	cp -a $(@D)/handai $(TARGET_DIR)/opt/handai/handai
	$(INSTALL) -D -m 0755 $(HANDAI_PKGDIR)/handai-launcher \
		$(TARGET_DIR)/usr/bin/handai
	$(INSTALL) -d $(TARGET_DIR)/etc/handai
	# seed the on-device config from the example if the overlay didn't provide one
	test -f $(TARGET_DIR)/etc/handai/handai.json || \
		$(INSTALL) -m 0644 $(@D)/config/handai.example.json \
			$(TARGET_DIR)/etc/handai/handai.json
endef

$(eval $(generic-package))
