# The wine-msvc lane image: the genuine MSVC toolchain under wine, and
# everything `buildutil build --compiler wine-msvc` needs, from upstream
# sources only.
#
#   docker build -t buildutil/msvc-wine:latest images/msvc-wine
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# wine runs cl; msitools unpacks what vsdownload fetches; clang is the
# reflect extension's parser (libclang's wheel ships no builtin headers,
# so a real clang has to be here); the rest is the build itself.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git python3 python3-venv python3-pip \
      wine wine64 msitools xz-utils \
      cmake ninja-build clang libclang-dev \
 && rm -rf /var/lib/apt/lists/*

# mstorsjo/msvc-wine downloads the toolchain from Microsoft and writes the
# unix wrappers (cl, link, lib, and the msvcenv.sh beside them that both
# the wrappers and buildutil's lane probe read).
ARG MSVC_WINE_REF=master
ARG VSDOWNLOAD_ARGS=""
RUN git clone --depth 1 --branch "${MSVC_WINE_REF}" \
      https://github.com/mstorsjo/msvc-wine /opt/msvc-wine \
 && python3 /opt/msvc-wine/vsdownload.py --accept-license \
      --dest /opt/msvc ${VSDOWNLOAD_ARGS} \
 && /opt/msvc-wine/install.sh /opt/msvc \
 && rm -rf /opt/msvc-wine /root/.cache

ENV PATH=/opt/msvc/bin/x64:$PATH
# _CL_ APPENDS where CL replaces: /Z7 keeps debug info in the .obj, so
# mspdbsrv -- whose RPC pipe hangs under wine -- never spawns. The driver
# sets it per build too; here it holds for a hand-run cl as well.
ENV _CL_=/Z7

CMD ["/bin/bash"]
