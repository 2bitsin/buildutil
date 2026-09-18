# Reflection — the `reflect` extension

C++ still has no reflection, and the shapes a program most wants to
describe — a command's options, a message on the wire, a settings record
— are exactly the ones a hand-written table goes stale against. The
`reflect` extension closes that gap: a type opts in with one line inside
itself, buildutil parses the header with libclang at build time, and the
generated schemes arrive **with the class**, wherever the header is
included from.

Declare the extension with `buildutil extend reflect`, which writes
`[cmake] extensions`. The generator parses C++ with libclang, so a
project that declares it needs **a clang on `PATH`**: buildutil installs
the bindings, but that wheel ships the library with none of clang's
builtin headers, and the resource directory they live in can only come
from a real clang or from `BUILDUTIL_RESOURCE_DIR`. Without one buildutil
says so and stops, rather than parsing every header without `stddef.h`
and reporting the wreckage as a missing project include.

## Opting in

```cpp
struct HelloWorld: Command
{
  friend constexpr auto reflect_scheme(HelloWorld*);

  bool        verbose { false };   /* say more about what is happening */
  std::string name    { "x" };     // a trailing run works too, and its
                                   // continuation lines are kept

  auto operator() (int  first,     /* the first one */
                   bool second     /* the second one */
                  ) const -> int;
};
```

Why a friend declaration rather than an attribute or a macro: it is real
C++, syntax-highlighted and pinned to the type; it needs no include,
because an unqualified elaborated friend declaration *is* a declaration;
it grants access to private members; and a typo in the type name is a
compile error rather than a silent no-op. It is also a fixed literal
string, so one grep selects both the files worth parsing and the types
within them. An attribute would warn on gcc and clang, and silencing that
on clang costs `-Wno-unknown-attributes`, which also stops catching
typo'd standard attributes.

Enums opt in too, but not with a friend — an enum has no
member-declaration list to attach one to, and needs none, since
enumerators are public:

```cpp
enum class Colour : std::int16_t { RED = 1, /* the warm one */
                                   BLUE };
constexpr auto reflect_scheme(Colour*);      // beside it, not inside it
```

## Reading a scheme

```cpp
constexpr auto S = reflect::scheme_of<HelloWorld>();
static_assert(reflect::scheme_size(S) == 2);
static_assert(decltype(reflect::scheme_item<0>(S))::NAME_STRING == "verbose");
static_assert(decltype(reflect::scheme_item<0>(S))::COMMENT ==
              "say more about what is happening");

constexpr auto C = reflect::call_scheme_of<HelloWorld>();       // parameters
static_assert(decltype(reflect::scheme_item<1>(C))::COMMENT == "the second one");
static_assert(reflect::scheme_size(C) ==                        // stale-check
              reflect::call_traits_of<HelloWorld>::ARITY);

constexpr auto E = reflect::scheme_of<Colour>();
static_assert(decltype(reflect::scheme_item<0>(E))::NAME_STRING == "RED");
static_assert(decltype(reflect::scheme_item<0>(E))::VALUE == Colour::RED);

constexpr auto B = reflect::bases_of<HelloWorld>();   // base_list<Command>
static_assert(std::is_same_v<reflect::base_at<0, decltype(B)>, Command>);
```

Each `member_scheme` carries `NAME_STRING`, `REFERENCE` (the
pointer-to-member), `COMMENT` and `ENCAPSULATED`; an `enumerator` carries
`NAME_STRING`, `VALUE` and `COMMENT`, verbatim — turning `OPTION_UNO`
into `--option-uno` is a CLI decision, not a reflection one. Parameter
*types* come from `call_traits_of`, so the generator never names a type
and its output is portable to MSVC and apple-clang. Members, methods and
enumerators are emitted in declaration order.

A type that derives from something answers with a `derived_scheme`
carrying a `base_list` alongside the same members, so `scheme_size` and
`scheme_item` still see the members and nothing written against
`class_scheme` has to change. A base arrives as a **type**, not a value,
because bases are routinely abstract. Only direct, public, non-virtual
bases are captured, a listed base need not be reflected itself, and
whether one is worth descending into is `reflected<Base>` — the
consumer's question rather than the generator's.

`reflected<T>` reports honestly, and `scheme_of` on an unreflected type
is a **compile error**: a command whose code generation never ran must
not quietly become a command with no options.

What the generator will not do is guess. An overloaded method name is
skipped with a warning, because reflection needs one address per name; a
class template with a non-type or template template parameter is refused
entirely; and a shortfall between the types the scan promised and the
schemes that came out writes **nothing** and reports the missing types,
the include directories in force and the compiler's own diagnostics.

## The detour

For `sources/<rel>/x.hpp`, buildutil emits `<generated>/<rel>/x.reflect.hpp`
with the schemes and a three-line **detour** at `<generated>/<rel>/x.hpp`
— at the same `sources`-relative path — whose whole content is the real
header by absolute path, then those schemes. The generated root goes
*ahead* of `sources/` on every include path, so the `#include` already
written resolves to the detour, and any translation unit that has the
class has its schemes, however many levels of `#include` away it was
reached. The absolute path is deliberate: `#include_next` is not portable
to MSVC.

**The header is never touched**: nothing under `sources/` is generated,
the real header is parsed in place, and an error or a go-to-definition in
class code lands in the file being edited.

One spelling reaches a reflected header — its `sources`-relative one,
`#include <demo/base/options.hpp>`. A quoted `#include "options.hpp"`
from the file next door searches its own directory first, finds the real
header and skips the detour; a bare `#include <options.hpp>` is answered
by the module's own include root and does the same. Both are a
**configure-time error** naming the file, the line and the spelling to
use, because that miss is exactly the one that used to ship: since
`reflected<T>` is a requires-expression, the miss answered *no* rather
than failing, the same class instantiated a consumer's templates two ways
in two translation units, and the linker picked one.

A **packaged** project ships the same arrangement, flattened into the one
include root a package has: `include/<rel>/x.hpp` is the detour, the real
header sits beside it as `x.detoured.hpp`, and `x.reflect.hpp` is beside
both. A consumer writes the include it always wrote and gets the schemes,
and a consumer that does not use the extension itself still compiles,
because the support surface ships too.

A scheme written by hand wins. Where a header both tags a type and
*defines* `reflect_scheme(T*)` for it — a body, not a `;` — the generator
emits nothing for that type and counts it answered rather than owed. It
is per type, and a header whose tagged types are all defined that way is
not claimed at all: no schemes, no detour.

## Annotations

Two spellings, one meaning. The ground truth is a real C++ attribute in
buildutil's own namespace, which every compiler parses and ignores:

```cpp
struct [[buildutil::label("endpoint")]] Endpoint
{
  friend constexpr auto reflect_scheme(Endpoint*);
  bool tls [[buildutil::label("secure")]] { false };
  int  port [[buildutil::meta("wire", "hot")]] { 80 };
};
enum class Role { SYSTEM [[buildutil::label("system")]], USER };
```

The sugar is `_Label(secure)`, `_Meta(wire, hot)` and `_Help("Use TLS")`,
defined in the generated macro header. A **label** is an alternative
external name — the name a declaration carries when it leaves the
program. **Meta** tags are free-form text, captured verbatim and in order
and never interpreted here: what a tag means belongs to the library that
reads it. They reach the schemes as `LABEL` and `tags`, and a type's own
annotation as `reflect::type_scheme_of<T>()`.

`_Help` sets a scheme's `COMMENT` on members, methods, parameters and
enumerators, overriding both leading and trailing comments, including
when its text is empty. Without it the trailing comment wins, with a
leading comment as fallback. The help tip and the source comment are two
different things: an author who wants both writes `_Help` and leaves the
comment free for a developer note. Two help annotations on one
declaration are an error, and types do not support `_Help` at all.

A **leading** comment is read as help only when it is immediately above
the declaration — a blank line detaches it — and at most three lines
long. A design note written above a member is adjacent to it and is not
about it, and without the second half of that rule a forty-line note
rendered verbatim under one option on the help screen. A trailing comment
has no such cap: it starts on the declaration's own line, so what it
belongs to was never in doubt.

The generator reads both spellings out of the **raw token stream**,
before macro expansion, which is why `[reflect] annotation` can move
without anything else moving. `macro`, the default, expands the macros to
nothing; `attribute` expands them to the real thing, and buildutil then
adds the suppression an unknown attribute needs to that project's
compiles. Opt into `attribute` only once every site uses a position an
attribute is legal in — after the class-key for a class or an enum,
*trailing* for an enumerator, either side of a member, a method or a
parameter — because the prefix form on an enumerator is ill-formed once
expanded.

An attribute in the `buildutil::` namespace that buildutil does not
define is a hard error naming the attribute and the valid set, which
recovers the typo safety clang's blunt suppression gives up. Attributes
in anyone else's namespace are ignored. `[reflect] macros = "none"`
writes no macro header at all, and a path names the project's own file —
the generator reads the spellings back out of it, so custom spellings and
what the generator recognises cannot drift apart.

## Parsing

libclang parses a translation unit **directly**, so it gets none of the
favours the clang *driver* does, and the driver is what supplies the
search paths. Two are put back by hand: `-resource-dir` from `clang
-print-resource-dir` for the builtin headers, and on macOS `-isysroot`
from `SDKROOT` or `xcrun --show-sdk-path`. The second is not optional
there — libc++ lives *inside* the SDK, and without it `<compare>`,
`<cstdint>` and the rest resolve to nothing and every tagged type
collapses. The parse also receives the consuming module's compile
definitions, so a type behind an `#ifdef` is there for the generator
exactly when it is there for the compiler.

A system libclang is preferred over the bundled wheel, because it matches
the compiler in use and because the wheel ships no resource headers, and
it is refused when its major is older than the bindings' or when the
library does not actually export the clang C API — an explicit load
probe, which is what caught a distribution shipping the wrong `.so` under
a plausible name. `BUILDUTIL_LIBCLANG` settles it outright.

**The parse runs on the host**, whatever the build targets, so a host
with no C++ toolchain loses every standard header. That is reported as
what it is — the generator names the standard headers it could not find
and says the project include path is not the cause — rather than as a
missing project include, which is what it used to look like. On the MSVC
lanes reflect targets `x86_64-pc-windows-msvc` with MSVC compatibility
and explicit system include paths for the target standard library and the
Windows SDK, so Windows headers are parsed against MSVC's standard
library even when a Linux libstdc++ is installed.

The scan honours `BUILD_TESTING` and `BUILD_BENCHMARKING`: `buildutil
build --no-tests` does not parse, gate or generate for headers under a
`*.test/` subtree.

## Configuration

```toml
[reflect]
namespace  = "reflect"   # baked into project source; changing it breaks consumers
annotation = "macro"     # macro | attribute
macros     = "auto"      # auto | none | <path to the project's own file>
```

The detour is the only delivery. `include = "source"` and `include =
"module"` — which pushed the reflect header into each `.cpp`, or into
each target — are gone, along with the `scan` key that only chose how
they mapped a `.cpp` to its headers, and a project still carrying either
is refused with a message rather than quietly built the other way.
