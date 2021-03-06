# Copyright (c) 2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from contextlib import nested
from mock import patch, MagicMock
import unittest

from oss2swift.cfg import CONF
from oss2swift.request import OssAclRequest, Request, X_OSS_DATE_FORMAT2, X_OSS_DATE_FORMAT
from oss2swift.request import Request as Oss_Request
from oss2swift.response import InvalidArgument, NoSuchBucket, InternalError, \
    AccessDenied, SignatureDoesNotMatch
from oss2swift.subresource import ACL, User, Owner, Grant, encode_acl
from oss2swift.test.unit.test_middleware import Oss2swiftTestCase
from oss2swift.utils import mktime
from swift.common import swob
from swift.common.swob import Request, HTTPNoContent


Fake_ACL_MAP = {
    # HEAD Bucket
    ('HEAD', 'HEAD', 'container'):
    {'Resource': 'container',
     'Permission': 'READ'},
    # GET Bucket
    ('GET', 'GET', 'container'):
    {'Resource': 'container',
     'Permission': 'READ'},
    # HEAD Object
    ('HEAD', 'HEAD', 'object'):
    {'Resource': 'object',
     'Permission': 'READ'},
    # GET Object
    ('GET', 'GET', 'object'):
    {'Resource': 'object',
     'Permission': 'READ'},
}


def _gen_test_acl_header(owner, permission=None, grantee=None,
                         resource='container'):
    if permission is None:
        return ACL(owner, [])

    if grantee is None:
        grantee = User('test:tester')
    return encode_acl(resource, ACL(owner, [Grant(grantee, permission)]))


class FakeResponse(object):
    def __init__(self, oss_acl):
        self.sysmeta_headers = {}
        if oss_acl:
            owner = Owner(id='test:tester', name='test:tester')
            self.sysmeta_headers.update(
                _gen_test_acl_header(owner, 'FULL_CONTROL',
                                     resource='container'))
            self.sysmeta_headers.update(
                _gen_test_acl_header(owner, 'FULL_CONTROL',
                                     resource='object'))


class FakeSwiftResponse(object):
    def __init__(self):
        self.environ = {
            'PATH_INFO': '/v1/AUTH_test',
            'HTTP_X_TENANT_NAME': 'test',
            'HTTP_X_USER_NAME': 'tester',
            'HTTP_X_AUTH_TOKEN': 'token',
        }


class TestRequest(Oss2swiftTestCase):

    def setUp(self):
        super(TestRequest, self).setUp()
        CONF.oss_acl = True

    def tearDown(self):
        CONF.oss_acl = False

    @patch('oss2swift.acl_handlers.ACL_MAP', Fake_ACL_MAP)
    @patch('oss2swift.request.OssAclRequest.authenticate', lambda x, y: None)
    def _test_get_response(self, method, container='bucket', obj=None,
                           permission=None, skip_check=False,
                           req_klass=Oss_Request, fake_swift_resp=None):
        path = '/' + container + ('/' + obj if obj else '')
        req = Request.blank(path,
                            environ={'REQUEST_METHOD': method},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header()})
        if issubclass(req_klass, OssAclRequest):
            oss_req = req_klass(req.environ, MagicMock())
        else:
            oss_req = req_klass(req.environ)
        with nested(patch('oss2swift.request.Request._get_response'),
                    patch('oss2swift.subresource.ACL.check_permission')) \
                as (mock_get_resp, m_check_permission):
            mock_get_resp.return_value = fake_swift_resp \
                or FakeResponse(CONF.oss_acl)
            return mock_get_resp, m_check_permission,\
                oss_req.get_response(self.oss2swift)

    def test_get_response_without_oss_acl(self):
        with patch('oss2swift.cfg.CONF.oss_acl', False):
            mock_get_resp, m_check_permission, oss_resp = \
                self._test_get_response('HEAD')
        self.assertFalse(hasattr(oss_resp, 'bucket_acl'))
        self.assertFalse(hasattr(oss_resp, 'object_acl'))
        self.assertEqual(mock_get_resp.call_count, 1)
        self.assertEqual(m_check_permission.call_count, 0)

    def test_get_response_without_match_ACL_MAP(self):
        with self.assertRaises(Exception) as e:
            self._test_get_response('POST', req_klass=OssAclRequest)
        self.assertEqual(e.exception.message,
                         'No permission to be checked exists')

    def test_get_response_without_duplication_HEAD_request(self):
        obj = 'object'
        mock_get_resp, m_check_permission, oss_resp = \
            self._test_get_response('HEAD', obj=obj,
                                    req_klass=OssAclRequest)
        self.assertTrue(oss_resp.bucket_acl is not None)
        self.assertTrue(oss_resp.object_acl is not None)
        self.assertEqual(mock_get_resp.call_count, 1)
        args, kargs = mock_get_resp.call_args_list[0]
        get_resp_obj = args[3]
        self.assertEqual(get_resp_obj, obj)
        self.assertEqual(m_check_permission.call_count, 1)
        args, kargs = m_check_permission.call_args
        permission = args[1]
        self.assertEqual(permission, 'READ')

    def test_get_response_with_check_object_permission(self):
        obj = 'object'
        mock_get_resp, m_check_permission, oss_resp = \
            self._test_get_response('GET', obj=obj,
                                    req_klass=OssAclRequest)
        self.assertTrue(oss_resp.bucket_acl is not None)
        self.assertTrue(oss_resp.object_acl is not None)
        self.assertEqual(mock_get_resp.call_count, 2)
        args, kargs = mock_get_resp.call_args_list[0]
        get_resp_obj = args[3]
        self.assertEqual(get_resp_obj, obj)
        self.assertEqual(m_check_permission.call_count, 1)
        args, kargs = m_check_permission.call_args
        permission = args[1]
        self.assertEqual(permission, 'READ')

    def test_get_response_with_check_container_permission(self):
        mock_get_resp, m_check_permission, oss_resp = \
            self._test_get_response('GET',
                                    req_klass=OssAclRequest)
        self.assertTrue(oss_resp.bucket_acl is not None)
        self.assertTrue(oss_resp.object_acl is not None)
        self.assertEqual(mock_get_resp.call_count, 2)
        args, kargs = mock_get_resp.call_args_list[0]
        get_resp_obj = args[3]
        self.assertTrue(get_resp_obj is '')
        self.assertEqual(m_check_permission.call_count, 1)
        args, kargs = m_check_permission.call_args
        permission = args[1]
        self.assertEqual(permission, 'READ')

    def test_get_validate_param(self):
        def create_ossrequest_with_param(param, value):
            req = Request.blank(
                '/bucket?%s=%s' % (param, value),
                environ={'REQUEST_METHOD': 'GET'},
                headers={'Authorization': 'OSS test:tester:hmac',
                         'Date': self.get_date_header()})
            return Oss_Request(req.environ, True)

        ossreq = create_ossrequest_with_param('max-keys', '1')

        # a param in the range
        self.assertEqual(ossreq.get_validated_param('max-keys', 1000, 1000), 1)
        self.assertEqual(ossreq.get_validated_param('max-keys', 0, 1), 1)

        # a param in the out of the range
        self.assertEqual(ossreq.get_validated_param('max-keys', 0, 0), 0)

        # a param in the out of the integer range
        ossreq = create_ossrequest_with_param('max-keys', '1' * 30)
        with self.assertRaises(InvalidArgument) as result:
            ossreq.get_validated_param('max-keys', 1)
        self.assertTrue(
            'not an integer or within integer range' in result.exception.body)
        self.assertEqual(
            result.exception.headers['content-type'], 'application/xml')

        # a param is negative integer
        ossreq = create_ossrequest_with_param('max-keys', '-1')
        with self.assertRaises(InvalidArgument) as result:
            ossreq.get_validated_param('max-keys', 1)
        self.assertTrue(
            'must be an integer between 0 and' in result.exception.body)
        self.assertEqual(
            result.exception.headers['content-type'], 'application/xml')

        # a param is not integer
        ossreq = create_ossrequest_with_param('max-keys', 'invalid')
        with self.assertRaises(InvalidArgument) as result:
            ossreq.get_validated_param('max-keys', 1)
        self.assertTrue(
            'not an integer or within integer range' in result.exception.body)
        self.assertEqual(
            result.exception.headers['content-type'], 'application/xml')

    def test_authenticate_delete_Authorization_from_ossreq_headers(self):
        req = Request.blank('/bucket/obj',
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header()})
        with nested(patch.object(Request, 'get_response'),
                    patch.object(Request, 'remote_user', 'authorized')) \
                as (m_swift_resp, m_remote_user):

            m_swift_resp.return_value = FakeSwiftResponse()
            oss_req = Oss_Request(req.environ,MagicMock())
            self.assertTrue('HTTP_AUTHORIZATION'  in oss_req.environ)
            self.assertTrue('Authorization' in oss_req.headers)
           # self.assertEqual(oss_req.token, 'token')

    def test_to_swift_req_Authorization_not_exist_in_swreq_headers(self):
        container = 'bucket'
        obj = 'obj'
        method = 'GET'
        req = Request.blank('/%s/%s' % (container, obj),
                            environ={'REQUEST_METHOD': method},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header()})
        with nested(patch.object(Request, 'get_response'),
                    patch.object(Request, 'remote_user', 'authorized')) \
                as (m_swift_resp, m_remote_user):

            m_swift_resp.return_value = FakeSwiftResponse()
            oss_req = Oss_Request(req.environ,MagicMock())
            sw_req = oss_req.to_swift_req(method, container, obj)
            self.assertIn('HTTP_AUTHORIZATION', sw_req.environ)
            self.assertIn('Authorization', sw_req.headers)
           # self.assertEqual(sw_req.headers['X-Auth-Token'], 'token')

    def test_to_swift_req_subrequest_proxy_access_log(self):
        container = 'bucket'
        obj = 'obj'
        method = 'GET'

        # force_swift_request_proxy_log is True
        req = Request.blank('/%s/%s' % (container, obj),
                            environ={'REQUEST_METHOD': method,
                                     'swift.proxy_access_log_made': True},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header()})
        with nested(patch.object(Request, 'get_response'),
                    patch.object(Request, 'remote_user', 'authorized'),
                    patch('oss2swift.cfg.CONF.force_swift_request_proxy_log',
                    True)) \
                as (m_swift_resp, m_remote_user, m_cfg):

            m_swift_resp.return_value = FakeSwiftResponse()
            oss_req = Oss_Request(req.environ, MagicMock())
            sw_req = oss_req.to_swift_req(method, container, obj)
            self.assertFalse(sw_req.environ['swift.proxy_access_log_made'])

        # force_swift_request_proxy_log is False
        req = Request.blank('/%s/%s' % (container, obj),
                            environ={'REQUEST_METHOD': method,
                                     'swift.proxy_access_log_made': True},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header()})
        with nested(patch.object(Request, 'get_response'),
                    patch.object(Request, 'remote_user', 'authorized')) \
                as (m_swift_resp, m_remote_user):

            m_swift_resp.return_value = FakeSwiftResponse()
            oss_req = Oss_Request(req.environ, MagicMock())
            sw_req = oss_req.to_swift_req(method, container, obj)
            self.assertTrue(sw_req.environ['swift.proxy_access_log_made'])

    def test_get_container_info(self):
        self.swift.register('HEAD', '/v1/AUTH_test/bucket', HTTPNoContent,
                            {'x-container-read': 'foo',
                             'X-container-object-count': 5,
                             'X-container-meta-foo': 'bar'}, None)
        req = Request.blank('/bucket', environ={'REQUEST_METHOD': 'GET'},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header()})
        oss_req = Oss_Request(req.environ, True)
        # first, call get_response('HEAD')
        info = oss_req.get_container_info(self.app)
        self.assertTrue('status' in info)  # sanity
        self.assertEqual(204, info['status'])  # sanity
        self.assertEqual('foo', info['read_acl'])  # sanity
        self.assertEqual('5', info['object_count'])  # sanity
        self.assertEqual({'foo': 'bar'}, info['meta'])  # sanity
        with patch('oss2swift.request.get_container_info',
                   return_value={'status': 204}) as mock_info:
            # Then all calls goes to get_container_info
            for x in xrange(10):
                info = oss_req.get_container_info(self.swift)
                self.assertTrue('status' in info)  # sanity
                self.assertEqual(204, info['status'])  # sanity
            self.assertEqual(10, mock_info.call_count)

        expected_errors = [(404, NoSuchBucket), (0, InternalError)]
        for status, expected_error in expected_errors:
            with patch('oss2swift.request.get_container_info',
                       return_value={'status': status}):
                self.assertRaises(
                    expected_error, oss_req.get_container_info, MagicMock())

    def test_date_header_missing(self):
        self.swift.register('HEAD', '/v1/AUTH_test/nojunk', swob.HTTPNotFound,
                            {}, None)
        req = Request.blank('/nojunk',
                            environ={'REQUEST_METHOD': 'HEAD'},
                            headers={'Authorization': 'OSS test:tester:hmac'})
        status, headers, body = self.call_oss2swift(req)
        self.assertEqual(status.split()[0], '403')
        self.assertEqual(body, '')

    def test_date_header_expired(self):
        self.swift.register('HEAD', '/v1/AUTH_test/nojunk', swob.HTTPNotFound,
                            {}, None)
        req = Request.blank('/nojunk',
                            environ={'REQUEST_METHOD': 'HEAD'},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': 'Fri, 01 Apr 2014 12:00:00 GMT'})

        status, headers, body = self.call_oss2swift(req)
        self.assertEqual(status.split()[0], '403')
        self.assertEqual(body, '')

    def test_date_header_with_x_oss_date_valid(self):
        self.swift.register('HEAD', '/v1/AUTH_test/nojunk', swob.HTTPNotFound,
                            {}, None)
        req = Request.blank('/nojunk',
                            environ={'REQUEST_METHOD': 'HEAD'},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': 'Fri, 01 Apr 2014 12:00:00 GMT',
                                     'x-oss-date': self.get_date_header()})

        status, headers, body = self.call_oss2swift(req)
        self.assertEqual(status.split()[0], '404')
        self.assertEqual(body, '')

    def test_date_header_with_x_oss_date_expired(self):
        self.swift.register('HEAD', '/v1/AUTH_test/nojunk', swob.HTTPNotFound,
                            {}, None)
        req = Request.blank('/nojunk',
                            environ={'REQUEST_METHOD': 'HEAD'},
                            headers={'Authorization': 'OSS test:tester:hmac',
                                     'Date': self.get_date_header(),
                                     'x-oss-date':
                                     'Fri, 01 Apr 2014 12:00:00 GMT'})

        status, headers, body = self.call_oss2swift(req)
        self.assertEqual(status.split()[0], '403')
        self.assertEqual(body, '')

    def _test_request_timestamp_sigv2(self, date_header):
        # signature v4 here
        environ = {
            'REQUEST_METHOD': 'GET'}

        headers = {'Authorization': 'OSS test:tester:hmac'}
        headers.update(date_header)
        req = Request.blank('/', environ=environ, headers=headers)
        sigv2_req = Oss_Request(req.environ)

        if 'X-Oss-Date' in date_header:
            timestamp = mktime(req.headers.get('X-Oss-Date'))
        elif 'Date' in date_header:
            timestamp = mktime(req.headers.get('Date'))
        else:
            self.fail('Invalid date header specified as test')
        self.assertEqual(timestamp, int(sigv2_req.timestamp))

    def test_request_timestamp_sigv2(self):
        access_denied_message = \
            'OSS authentication requires a valid Date or x-oss-date header'

        # In v2 format, normal X-Oss-Date header is same
        date_header = {'X-Oss-Date': self.get_date_header()}
        self._test_request_timestamp_sigv2(date_header)

        # normal Date header
        date_header = {'Date': self.get_date_header()}
        self._test_request_timestamp_sigv2(date_header)

        # mangled X-Oss-Date header
        date_header = {'X-Oss-Date': self.get_date_header()[:-20]}
        with self.assertRaises(AccessDenied) as cm:
            self._test_request_timestamp_sigv2(date_header)

        self.assertEqual('403 Forbidden', cm.exception.message)
        self.assertIn(access_denied_message, cm.exception.body)

        # mangled Date header
        date_header = {'Date': self.get_date_header()[:-20]}
        with self.assertRaises(AccessDenied) as cm:
            self._test_request_timestamp_sigv2(date_header)

        self.assertEqual('403 Forbidden', cm.exception.message)
        self.assertIn(access_denied_message, cm.exception.body)

    def test_canonical_uri_sigv2(self):
        environ = {
            'HTTP_HOST': 'bucket1.oss.test.com',
            'REQUEST_METHOD': 'GET'}

        headers = {'Authorization': 'OSS test:tester:hmac',
                   'X-Oss-Date': self.get_date_header()}

        # Virtual hosted-style
        with patch('oss2swift.cfg.CONF.storage_domain', 'oss.test.com'):
            req = Request.blank('/', environ=environ, headers=headers)
            sigv2_req = Oss_Request(req.environ)
            uri = sigv2_req._canonical_uri()
            self.assertEqual(uri, '/bucket1/')
            self.assertEqual(req.environ['PATH_INFO'], '/')

            req = Request.blank('/obj1', environ=environ, headers=headers)
            sigv2_req = Oss_Request(req.environ)
            uri = sigv2_req._canonical_uri()
            self.assertEqual(uri, '/bucket1/obj1')
            self.assertEqual(req.environ['PATH_INFO'], '/obj1')

        environ = {
            'HTTP_HOST': 'oss.test.com',
            'REQUEST_METHOD': 'GET'}

        # Path-style
        with patch('oss2swift.cfg.CONF.storage_domain', ''):
            req = Request.blank('/', environ=environ, headers=headers)
            sigv2_req = Oss_Request(req.environ)
            uri = sigv2_req._canonical_uri()

            self.assertEqual(uri, '/')
            self.assertEqual(req.environ['PATH_INFO'], '/')

            req = Request.blank('/bucket1/obj1',
                                environ=environ,
                                headers=headers)
            sigv2_req = Oss_Request(req.environ)
            uri = sigv2_req._canonical_uri()
            self.assertEqual(uri, '/bucket1/obj1')
            self.assertEqual(req.environ['PATH_INFO'], '/bucket1/obj1')

if __name__ == '__main__':
    unittest.main()

