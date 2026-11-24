# Asset cleanup / trash restore demo

Screenshots of the P1 phase:

- `delete-exclusive-images.png`: delete confirmation showing exclusive and
  shared image counts, with "同时删除仅被本文引用的图片" checked by default;
- `trash-restore.png`: the trash dialog with a restore action;
- `orphan-assets-cleanup.png`: the resource-cleanup dialog for unreferenced images.

The screenshots were taken from a static demo; the impact and trash values are
stubbed only for layout. Core asset-index, exclusive-image move, trash list and
restore behavior are covered by tests.
