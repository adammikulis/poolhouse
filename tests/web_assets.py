"""The page's own files, served to a playwright route the way the fleet daemon serves them."""

from poolhouse.fleet.routes import asset_bytes, ui_asset

_FOLDERS = (("/ui/ml-ui/", ui_asset), ("/ui/static/", asset_bytes))


def serve_page_asset(request, path):
    """Fulfil the route's request from the ml-ui or static folder; False when `path` is neither."""
    for prefix, find in _FOLDERS:
        if path.startswith(prefix):
            asset = find(path[len(prefix):])
            if asset is None:
                request.fulfill(status=404, body="{}", content_type="application/json")
            else:
                request.fulfill(body=asset[0], content_type=asset[1])
            return True
    return False
