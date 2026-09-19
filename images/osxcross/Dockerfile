# The osxcross lane image: clang wrappers around a packaged macOS SDK, and
# everything `buildutil build --compiler osxcross` needs, from upstream
# sources only.
#
# The ONE input that cannot be downloaded is the SDK itself: Apple's licence
# does not allow redistributing it, and `osxcross/tools/gen_sdk_package.sh`
# has to run on a Mac with Xcode. Put the tarball it writes in
# `tarballs/` beside this file (gitignored) and build:
#
#   docker build -t buildutil/osxcross:latest images/osxcross
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# The first group is what osxcross itself needs to build cctools/ld64, the
# second is the lane: lld because osxcross's ld64 does not synthesise
# clang's arm64 objc_msgSend selector stubs, clang because the reflect
# extension parses headers with libclang and the wheel ships no builtin
# headers, cmake/ninja/python3 because that is the build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git patch make sed cpio bzip2 gzip xz-utils \
      libxml2-dev uuid-dev libssl-dev libbz2-dev zlib1g-dev llvm-dev \
      lld clang libclang-dev cmake ninja-build python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

ARG OSXCROSS_REF=master
RUN git clone --depth 1 --branch "${OSXCROSS_REF}" \
      https://github.com/tpoechtrager/osxcross /opt/osxcross-src
COPY tarballs/ /opt/osxcross-src/tarballs/
RUN cd /opt/osxcross-src \
 && UNATTENDED=1 TARGET_DIR=/opt/osxcross/target ./build.sh \
 && rm -rf /opt/osxcross-src/build /opt/osxcross-src/tarballs

ENV OSXCROSS_ROOT=/opt/osxcross/target
ENV PATH=/opt/osxcross/target/bin:$PATH
# Deliberately empty: the deployment target belongs to the project being
# built, and a value baked in here silently fights the one it sets.
ENV MACOSX_DEPLOYMENT_TARGET=

CMD ["/bin/bash"]
