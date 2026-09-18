# The direct-invocation guard, run via `cmake -P` from two places: an
# always-built custom target (so a bare `ninja` / `cmake --build` trips
# it) and a ctest setup fixture (so a bare `ctest` trips it). buildutil
# exports BUILDUTIL=<version> to every child process; a tool invoked by
# hand has no such parent and is refused with the explanation instead
# of half-working around the driver's contracts.
if(NOT DEFINED ENV{BUILDUTIL})
  message(FATAL_ERROR
    "this project is controlled by buildutil — cmake, ninja, ctest and "
    "conan are orchestrated, and driving one of them by hand skips the "
    "venv, the conan profile, the rendered machinery and the install "
    "contract. Use the driver instead:\n"
    "  buildutil build | test | bench | run | coverage | analyze\n"
    "If you really need the direct call, set BUILDUTIL=1 in the "
    "environment.")
endif()
