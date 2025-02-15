from logging import Logger, getLogger
from typing import Any, Dict

from gql import Client
from gql.dsl import DSLSchema
from gql.transport.httpx import HTTPXTransport
from gql.transport.exceptions import (
    TransportClosed,
    TransportProtocolError,
    TransportQueryError,
    TransportServerError,
    TransportAlreadyConnected,
)
from graphql import DocumentNode, GraphQLError, GraphQLSchema, build_ast_schema, parse
from httpx import ConnectTimeout, ReadTimeout

from isar_exr.api.authentication import get_access_token
from isar_exr.config.settings import settings


class GraphqlClient:
    def __init__(self) -> None:
        # Parameter used for retrying query with new authentication
        # in case of expired token
        self._reauthenticated: bool = False
        self.logger: Logger = getLogger("graphql_client")
        self._initialize_session()

    def _get_updated_auth_header(self) -> Dict:
        try:
            token: str = get_access_token()
        except Exception as e:
            self.logger.critical(f"CRITICAL - Error getting access token: \n{e}")
            raise
        auth_header: dict = {
            "authorization": "Bearer " + token,
        }
        return auth_header

    def _refresh_session(self) -> None:
        auth_header = self._get_updated_auth_header()
        transport: HTTPXTransport = HTTPXTransport(
            url=settings.ROBOT_API_URL, headers=auth_header
        )
        # self.session.transport = transport
        schema: GraphQLSchema = build_ast_schema(self.document)
        self.client = Client(transport=transport, schema=schema)
        self.session = self.client.connect_sync()

    def _initialize_session(self) -> None:
        auth_header = self._get_updated_auth_header()

        # Loading schema from file is recommended,
        # ref https://github.com/graphql-python/gql/issues/331
        with open(settings.PATH_TO_GRAPHQL_SCHEMA, encoding="utf-8") as source:
            self.document = parse(source.read())

        schema: GraphQLSchema = build_ast_schema(self.document)

        transport: HTTPXTransport = HTTPXTransport(
            url=settings.ROBOT_API_URL, headers=auth_header
        )
        self.client: Client = Client(transport=transport, schema=schema)  # type: ignore
        self.schema: DSLSchema = DSLSchema(self.client.schema)
        self.session = self.client.connect_sync()

    def query(
        self, query: DocumentNode, query_parameters: dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Sends a GraphQL query to the 'ROBOT_API_URL' endpoint.

        :return: A dictionary of the object returned from the API if success.

        :raises GrahpQLError: Something went related to the query
        :raises TransportError: Something went wrong during transfer or on the API server side
        :raises Exception: Unknown error
        """
        try:
            response: Dict[str, Any] = self.session.execute(query, query_parameters)
            return response
        except GraphQLError as e:
            self.logger.error(
                f"Something went wrong while sending the GraphQL query: {e.message}"
            )
            raise
        except TransportProtocolError as e:
            if self._reauthenticated:
                self.logger.error(
                    "Transport protocol error - Error in configuration of GraphQL client even after reauthentication"
                )
                raise
            else:
                # The token might have expired, try again with a new token
                self._refresh_session()
                self._reauthenticated = True
                return self.query(query=query, query_parameters=query_parameters)
        except TransportQueryError as e:
            self.logger.error(
                f"The Energy Robotics server returned an error: {e.errors}"
            )
            raise
        except TransportClosed as e:
            self.logger.error("The connection to the GraphQL endpoint is closed")
            raise
        except TransportServerError as e:
            if e.code == 302:
                if self._reauthenticated:
                    self.logger.error(
                        "Transport server error - Error in Energy Robotics server even after reauthentication"
                    )
                    raise
                else:
                    self._refresh_session()
                    self._reauthenticated = True
                    return self.query(query=query, query_parameters=query_parameters)
            else:
                self.logger.error(f"Error in Energy Robotics server: {e}")
                raise
        except TransportAlreadyConnected as e:
            self.logger.error(f"The transport is already connected: {e}")
            raise
        except ReadTimeout as e:
            self.logger.error(f"Request to GraphQL API timed out: {e}")
            raise
        except ConnectTimeout as e:
            self.logger.error(f"Connection to GraphQL API timed out: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unknown error in GraphQL client: {e}")
            raise
        finally:
            self._reauthenticated = False
