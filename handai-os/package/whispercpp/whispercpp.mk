################################################################################
#
# whispercpp
#
################################################################################

WHISPERCPP_VERSION = 1.8.4
WHISPERCPP_SITE = https://github.com/ggml-org/whisper.cpp/archive/refs/tags
WHISPERCPP_SOURCE = v$(WHISPERCPP_VERSION).tar.gz
WHISPERCPP_LICENSE = MIT
WHISPERCPP_LICENSE_FILES = LICENSE
WHISPERCPP_SUPPORTS_IN_SOURCE_BUILD = NO
WHISPERCPP_CONF_OPTS = \
	-DWHISPER_BUILD_TESTS=OFF \
	-DWHISPER_BUILD_EXAMPLES=ON \
	-DWHISPER_BUILD_SERVER=OFF \
	-DWHISPER_CURL=OFF \
	-DWHISPER_SDL2=OFF \
	-DWHISPER_FFMPEG=OFF \
	-DGGML_NATIVE=OFF

define WHISPERCPP_REMOVE_UNUSED_TARGET_FILES
	rm -f $(TARGET_DIR)/usr/bin/whisper-bench \
		$(TARGET_DIR)/usr/bin/whisper-server \
		$(TARGET_DIR)/usr/bin/whisper-quantize \
		$(TARGET_DIR)/usr/bin/whisper-vad-speech-segments
	rm -rf $(TARGET_DIR)/usr/include/ggml* $(TARGET_DIR)/usr/include/whisper.h \
		$(TARGET_DIR)/usr/lib/cmake/ggml $(TARGET_DIR)/usr/lib/cmake/whisper \
		$(TARGET_DIR)/usr/lib/pkgconfig/whisper.pc
endef
WHISPERCPP_POST_INSTALL_TARGET_HOOKS += WHISPERCPP_REMOVE_UNUSED_TARGET_FILES

$(eval $(cmake-package))
