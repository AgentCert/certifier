"""
AsyncFileStorage class for managing file storage in Azure Blob Storage.
"""

import asyncio
import os
import re

import yaml
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from utils import custom_errors
from utils.setup_logging import logger


class AsyncFileStorage:
    """
    AsyncFileStorage class for managing file storage in Azure Blob Storage.
    """

    def __init__(self, config):
        """
        Initialize the AsyncFileStorage class
        :param config: Configuration details containing connection details
        """
        self.__config = config
        self.connection = {}

    async def create_file_storage_connection(self):
        """Create a connection pool for each container."""
        for container_name, container_details in self.__config.get(
            "storage_connections", {}
        ).items():
            if container_details.get("isActive", False):
                try:
                    if container_details.get("account_name"):
                        account_name = container_details.get("account_name")
                        account_url = f"https://{account_name}.blob.core.windows.net"
                        credential = DefaultAzureCredential()
                        blob_service_client = BlobServiceClient(
                            account_url=account_url, credential=credential
                        )
                    else:
                        blob_service_client = BlobServiceClient.from_connection_string(
                            conn_str=container_details.get("connection_str"),
                            api_version="2020-10-02",
                        )
                    self.connection[container_name] = (
                        blob_service_client.get_container_client(container_name)
                    )

                    # Ensure the container exists
                    if not await self.connection[container_name].exists():
                        await self.connection[container_name].create_container()
                    logger.info(
                        f"Connection for Container: {container_name} created successfully"
                    )
                except custom_errors.MyCustomError as specific_error:
                    raise specific_error
                except Exception as e:
                    raise custom_errors.AsyncFileStorageError(
                        f"Error in {self.__class__.__name__}.create_file_storage_connection: "
                        f"{str(e)}",
                        e,
                    ) from e

    async def read_file(self, container_name, file_name):
        """
        Read a file from a container and return its contents.

        :param container_name: The name of the container where the file is stored
        :param file_name: The name of the file to read
        :return: Contents of the file as a string
        """
        if container_name not in self.connection:
            raise custom_errors.AsyncFileStorageError(
                f"Container {container_name} not connected"
            )
        try:

            blob_client = self.connection[container_name].get_blob_client(file_name)

            # Check if blob exists
            if not await blob_client.exists():
                raise custom_errors.AsyncFileStorageError(
                    f"File {file_name} not found in container {container_name}"
                )

            # Download the blob content
            download_stream = await blob_client.download_blob()
            file_content = await download_stream.readall()

            return file_content

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.AsyncFileStorageError(
                f"Error reading file {file_name} from {container_name}: {str(e)}", e
            ) from e

    async def upload_file(self, container_name, local_file_path, container_path=None):
        """Upload a file to a container in blob storage.

        :param container_name: The name of the container where the file should be uploaded
        :param local_file_path: Path to the local file to upload
        :param container_path: Optional path within the container
            (default: use filename from local_file_path)
        :return: URL of the uploaded file
        """
        try:
            if container_name not in self.connection:
                raise custom_errors.AsyncFileStorageError(
                    f"Container {container_name} not connected"
                )

            # If container_path is not provided, use the filename from local_file_path
            if not container_path:
                container_path = os.path.basename(local_file_path)

            # Get blob client for the target path
            blob_client = self.connection[container_name].get_blob_client(
                container_path
            )

            # Upload the file
            with open(local_file_path, "rb") as file_data:
                await blob_client.upload_blob(file_data, overwrite=True)

            logger.info(
                f"File {local_file_path} uploaded successfully to {container_name}/{container_path}"
            )

            # Return the URL of the uploaded blob
            return blob_client.url

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.AsyncFileStorageError(
                f"Error uploading file {local_file_path} to {container_name}/{container_path}: "
                f"{str(e)}",
                e,
            ) from e

    async def list_files(self, container_name, regex_pattern=None):
        """
        List all files in a container. If regex_pattern is provided, only return matching files.

        :param container_name: The name of the container to list files from
        :param regex_pattern: Optional regex pattern to filter files
        :return: List of file names in the container
        """

        try:
            if container_name not in self.connection:
                raise custom_errors.AsyncFileStorageError(
                    f"Container {container_name} not connected"
                )

            # Get the container client
            container_client = self.connection[container_name]

            # List all blobs in the container
            file_list = []
            async for blob in container_client.list_blobs():
                file_name = blob.name

                # If regex_pattern is provided, only include files that match
                if regex_pattern:
                    if re.match(regex_pattern, file_name):
                        file_list.append(file_name)
                else:
                    file_list.append(file_name)

            return file_list

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.AsyncFileStorageError(
                f"Error listing files from container {container_name}: {str(e)}", e
            ) from e

    async def delete_file(self, container_name, file_path):
        """
        Delete a file from a container.

        :param container_name: The name of the container where the file is stored
        :param file_path: The name of the file to delete
        :return: True if the file was deleted successfully, False otherwise
        """
        try:
            if container_name not in self.connection:
                raise custom_errors.AsyncFileStorageError(
                    f"Container {container_name} not connected"
                )

            blob_client = self.connection[container_name].get_blob_client(file_path)

            # Check if blob exists
            if not await blob_client.exists():
                logger.warning(
                    f"File {file_path} not found in container {container_name}"
                )
                return False

            # Delete the blob
            await blob_client.delete_blob()
            logger.info(f"File {file_path} deleted successfully from {container_name}")
            return True

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.AsyncFileStorageError(
                f"Error deleting file {file_path} from {container_name}: {str(e)}", e
            ) from e

    async def close(self):
        """
        Close all blob storage client connections to free up resources.
        This should be called when the file storage client is no longer needed.
        """
        try:
            for container_name, container_client in self.connection.items():
                try:
                    await container_client.close()
                    logger.info(
                        f"Connection for Container: {container_name} closed successfully"
                    )
                except Exception as e:
                    logger.error(
                        f"Error closing connection for Container: {container_name}: {str(e)}"
                    )

            # Clear the connection dictionary
            self.connection.clear()

        except custom_errors.MyCustomError as specific_error:
            raise specific_error
        except Exception as e:
            raise custom_errors.AsyncFileStorageError(
                f"Error in {self.__class__.__name__}.close: {str(e)}", e
            ) from e
