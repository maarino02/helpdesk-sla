"""Static files storage for production."""

from whitenoise.storage import CompressedManifestStaticFilesStorage


class StaticFilesStorage(CompressedManifestStaticFilesStorage):
    """WhiteNoise's storage, also rewriting the imports between JavaScript modules.

    Each file is copied with a hash of its content in the name (api.js ->
    api.1f2e3d.js) and the templates link the hashed names, so browsers can cache
    them forever. A module importing "./api.js" would still load the old unhashed
    name, though: this option makes collectstatic rewrite those imports too. It
    only recognises relative paths ("./", "../") in statements ending with ";".
    """

    support_js_module_import_aggregation = True
