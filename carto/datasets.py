import time
import json
from gettext import gettext as _

from pyrestcli.resources import Resource
from pyrestcli.fields import IntegerField, CharField, DateTimeField, BooleanField

from .exceptions import CartoException
from .file_import import FileImportJobManager
from .sync_tables import SyncTableJobManager
from .tables import TableManager
from .fields import TableField, UserField, PermissionField
from .paginators import CartoPaginator
from .resources import Manager


API_VERSION = "v1"
API_ENDPOINT = "api/{api_version}/viz/"

MAX_NUMBER_OF_RETRIES = 30
INTERVAL_BETWEEN_RETRIES_S = 5


class Dataset(Resource):
    """
    Represents a dataset in CARTO. Typically, that means there is a table in the PostgreSQL server associated to this object
    """
    active_child = None
    active_layer_id = CharField()
    attributions = None
    auth_tokens = CharField(many=True)
    children = None
    created_at = DateTimeField()
    description = CharField()
    display_name = CharField()
    external_source = None
    id = CharField()
    kind = CharField()
    license = None
    liked = BooleanField()
    likes = IntegerField()
    locked = BooleanField()
    map_id = CharField()
    name = CharField()
    next_id = None
    parent_id = CharField()
    permission = PermissionField()
    prev_id = None
    privacy = CharField()
    source = None
    stats = DateTimeField(many=True)
    synchronization = None
    table = TableField()
    tags = CharField(many=True)
    title = CharField()
    transition_options = None
    type = CharField()
    updated_at = DateTimeField()
    url = CharField()
    uses_builder_features = BooleanField()
    user = UserField()

    class Meta:
        collection_endpoint = API_ENDPOINT.format(api_version=API_VERSION)
        name_field = "name"


class DatasetManager(Manager):
    """
    Manager for the Dataset class
    """
    resource_class = Dataset
    json_collection_attribute = "visualizations"
    paginator_class = CartoPaginator

    def send(self, url, http_method, **client_args):
        """
        Send API request, taking into account that datasets are part of the visualization endpoint
        :param url: Endpoint URL
        :param http_method: The method used to make the request to the API
        :param client_args: Arguments to be sent to the auth client
        :return:
        :raise CartoException:
        """
        try:
            client_args = client_args or {}

            if "params" not in client_args:
                client_args["params"] = {}
            client_args["params"].update({"type": "table", "exclude_shared": "true"})

            return super(DatasetManager, self).send(url, http_method, **client_args)
        except Exception as e:
            raise CartoException(e)

    def create(self, url, interval=None, **import_args):
        """
        Creating a table means uploading a file or setting up a sync table
        :param url: URL to the file (both remote URLs or local paths are supported)
        :param interval: If not None, CARTO will try to set up a sync table against the (remote) URL
        :param import_args: Arguments to be sent to the import job when run
        :return: New dataset object
        :raise CartoException:
        """
        url = url.lower()

        if url.startswith("http") and interval is not None:
            manager = SyncTableJobManager(self.client)
        else:
            manager = FileImportJobManager(self.client)

        import_job = manager.create(url) if interval is None else manager.create(url, interval)
        import_job.run(**import_args)

        if import_job.get_id() is None:
            raise CartoException(_("Import API returned corrupt job details when creating dataset"))

        import_job.refresh()

        count = 0
        while import_job.state in ("enqueued", "pending", "uploading", "unpacking", "importing", "guessing") or (isinstance(manager, SyncTableJobManager) and import_job.state == "created"):
            if count >= MAX_NUMBER_OF_RETRIES:
                raise CartoException(_("Maximum number of retries exceeded when polling the import API for dataset creation"))
            time.sleep(INTERVAL_BETWEEN_RETRIES_S)
            import_job.refresh()
            count += 1

        if import_job.state == "failure":
            raise CartoException(_("Dataset creation was not successful because of failed import (error: {error}").format(error=json.dumps(import_job.get_error_text)))

        if (import_job.state != "complete" and import_job.state != "created" and import_job.state != "success") or import_job.success is False:
            raise CartoException(_("Dataset creation was not successful because of unknown import error"))

        if hasattr(import_job, "visualization_id") and import_job.visualization_id is not None:
            visualization_id = import_job.visualization_id
        else:
            table = TableManager(self.client).get(import_job.table_id)
            visualization_id = table.table_visualization.get_id() if table is not None else None

        try:
            return self.get(visualization_id) if visualization_id is not None else None
        except AttributeError:
            raise CartoException(_("Dataset creation was not successful because of unknown error"))
