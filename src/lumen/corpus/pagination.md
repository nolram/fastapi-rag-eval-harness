# Pagination

Collection endpoints return one page of results plus a cursor for the next page.

## Cursor pagination defaults

List endpoints return 25 items by default and accept `limit` up to 200. Each response includes
`next_cursor` and `has_more`; pass `cursor=<next_cursor>` to fetch the following page.

## Cursor expiration

A cursor expires 30 minutes after it is issued. An expired or malformed cursor returns
`400 invalid_cursor`, and the scan must restart from the first page instead of reusing it.

## Offset pagination limits

Offset pagination is deprecated as of version `2024-11-01`. It rejects `offset + limit` above 10000
with `400 offset_out_of_range`, and it is slower than cursor pagination on large collections.

## Stable sort order

The default order is `created_at:desc` with an `id:asc` tie-break, so pages do not shift while new
records are being written. The `sort` parameter accepts at most 3 fields.

## Filter parameters

Filters use `field=eq:value`, `field=in:v1,v2` and `field=gt:value` on the 14 documented fields;
any other field returns `400 unknown_filter`. The `expand` parameter accepts at most 2 relations.
