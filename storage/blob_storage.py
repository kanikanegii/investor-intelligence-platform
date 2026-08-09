import os

from azure.storage.blob.aio import BlobServiceClient

CONTAINER_NAME = "raw-filings"


def get_blob_service_client() -> BlobServiceClient:
    """
    Create the async Blob Storage client for persisting uploaded filing PDFs.

    Local disk (data/raw_pdfs/) is ephemeral -- AKS pods have no
    PersistentVolumeClaim, so anything written there is lost on the next
    pod restart or redeploy. This is the durable copy.
    """
    connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    return BlobServiceClient.from_connection_string(connection_string)


async def upload_pdf(blob_service_client: BlobServiceClient, file_name: str, data: bytes) -> str:
    """
    Persist a raw filing PDF to durable Blob Storage.

    Args:
        blob_service_client: The app-lifetime client (see main.py's lifespan).
        file_name: Original PDF filename, used as the blob name.
        data: Raw PDF bytes.

    Returns:
        The blob's URL.
    """
    container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    blob_client = container_client.get_blob_client(file_name)
    await blob_client.upload_blob(data, overwrite=True)
    return blob_client.url
