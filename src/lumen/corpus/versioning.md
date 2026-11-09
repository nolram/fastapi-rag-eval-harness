# API versioning

Lumen API versions are dates, pinned per request with a header.

## Version pinning with the header

Pin a version with `X-Lumen-Version: 2025-02-01`. A request that omits the header uses the
workspace's pinned default version. The oldest version still supported is `2023-06-01`.

## Deprecation notice period

A version deprecation is announced at least 180 days before the shutdown date. From the day of the
announcement, every response on that version carries `Deprecation: true` and a `Sunset` header.

## Breaking change policy

Removing a field, retyping a field, or changing what a field means is breaking and requires a new
version. Adding a field or a new enum value is additive and may ship without a version change.

## Supported version count

Exactly 2 versions are supported at any time: one current and one legacy. A request pinned to a
sunset version returns `410 sunset_version` and is not processed.

## Changelog and migration guides

The changelog and the per-version migration guides are published at `docs.lumen.example/changelog`.
Every migration guide lists each removed field together with its replacement.
