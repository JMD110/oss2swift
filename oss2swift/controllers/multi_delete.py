import sys

from oss2swift.cfg import CONF
from oss2swift.controllers.base import Controller, bucket_operation
from oss2swift.etree import Element, SubElement, fromstring, tostring, \
    XMLSyntaxError, DocumentInvalid
from oss2swift.response import HTTPOk, OssNotImplemented, NoSuchKey, \
    ErrorResponse, MalformedXML, UserKeyMustBeSpecified, AccessDenied
from oss2swift.utils import LOGGER
from swift.common.utils import public


MAX_MULTI_DELETE_BODY_SIZE = 61365


class MultiObjectDeleteController(Controller):
    """
    Handles Delete Multiple Objects, which is logged as a MULTI_OBJECT_DELETE
    operation in the OSS server log.
    """
    def _gen_error_body(self, error, elem, delete_list):
        for key, version in delete_list:
            if version is not None:
                # TODO: delete the specific version of the object
                raise OssNotImplemented()

            error_elem = SubElement(elem, 'Error')
            SubElement(error_elem, 'Key').text = key
            SubElement(error_elem, 'Code').text = error.__class__.__name__
            SubElement(error_elem, 'Message').text = error._msg

        return tostring(elem)

    @public
    @bucket_operation
    def POST(self, req):
        """
        Handles Delete Multiple Objects.
        """
        def object_key_iter(elem):
            for obj in elem.iterchildren('Object'):
                key = obj.find('./Key').text
                if not key:
                    raise UserKeyMustBeSpecified()
                version = obj.find('./VersionId')
                if version is not None:
                    version = version.text

                yield key, version

        try:
            xml = req.xml(MAX_MULTI_DELETE_BODY_SIZE, check_md5=True)
            elem = fromstring(xml, 'Delete')

            quiet = elem.find('./Quiet')
            if quiet is not None and quiet.text.lower() == 'true':
                self.quiet = True
            else:
                self.quiet = False

            delete_list = list(object_key_iter(elem))
            if len(delete_list) > CONF.max_multi_delete_objects:
                raise MalformedXML()
        except (XMLSyntaxError, DocumentInvalid):
            raise MalformedXML()
        except ErrorResponse:
            raise
        except Exception as e:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            LOGGER.error(e)
            raise exc_type, exc_value, exc_traceback

        elem = Element('DeleteResult')

        # check bucket existence
        try:
            req.get_response(self.app, 'HEAD')
        except AccessDenied as error:
            body = self._gen_error_body(error, elem, delete_list)
            return HTTPOk(body=body)

        for key, version in delete_list:
            if version is not None:
                # TODO: delete the specific version of the object
                raise OssNotImplemented()

            req.object_name = key

            try:
                query = req.gen_multipart_manifest_delete_query(self.app)
                req.get_response(self.app, method='DELETE', query=query)
            except NoSuchKey:
                pass
            except ErrorResponse as e:
                error = SubElement(elem, 'Error')
                SubElement(error, 'Key').text = key
                SubElement(error, 'Code').text = e.__class__.__name__
                SubElement(error, 'Message').text = e._msg
                continue

            if not self.quiet:
                deleted = SubElement(elem, 'Deleted')
                SubElement(deleted, 'Key').text = key

        body = tostring(elem)

        return HTTPOk(body=body)
