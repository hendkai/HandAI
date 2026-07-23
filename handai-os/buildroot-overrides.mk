# Keep the embedded Node runtime new enough for the current OpenClaw release.
# This file is loaded through BR2_PACKAGE_OVERRIDE_FILE before Buildroot's
# package definitions; `override` intentionally wins over nodejs.mk's pinned
# common version.
override NODEJS_COMMON_VERSION := 22.22.3
