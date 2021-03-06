
import json

from flask import current_app, request, jsonify, make_response
from flask_cors import cross_origin

from alerta.auth.utils import is_authorized, create_token, get_customer
from alerta.utils.api import absolute_url, deepmerge
from . import auth

try:
    import saml2
    import saml2.entity
    import saml2.metadata
    import saml2.config
    import saml2.client
    import saml2.saml
except ImportError:
    pass  # saml2 authentication will not work


def spConfig():
    return saml2.config.Config()


def saml_client():

    saml2_config_default = {
        'entityid': absolute_url(),
        'service': {
            'sp': {
                'endpoints': {
                    'assertion_consumer_service': [
                        (absolute_url('/auth/saml'), saml2.BINDING_HTTP_POST)
                    ]
                }
            }
        }
    }
    spConfig().load(deepmerge(saml2_config_default, current_app.config['SAML2_CONFIG']))
    return saml2.client.Saml2Client(config=spConfig())


@auth.route('/auth/saml', methods=['GET'])
def saml_redirect_to_idp():
    relay_state = None if request.args.get('usePostMessage') is None else 'usePostMessage'
    (session_id, result) = saml_client().prepare_for_authenticate(relay_state=relay_state)
    return make_response('', 302, result['headers'])


@auth.route('/auth/saml', methods=['OPTIONS', 'POST'])
@cross_origin(supports_credentials=True)
def saml_response_from_idp():
    def _make_response(resp_obj, resp_code):
        if 'usePostMessage' in request.form.get('RelayState', '') and 'text/html' in request.headers.get('Accept', ''):
            origins = current_app.config.get('CORS_ORIGINS', [])
            response = make_response(
                '''<!DOCTYPE html>
                    <html lang="en">
                        <head>
                            <meta charset="UTF-8">
                            <title>Authenticating...</title>
                            <script type="application/javascript">
                                var origins = {origins};
                                // in case when API and WebUI are on the same origin
                                if (origins.indexOf(window.location.origin) < 0)
                                    origins.push(window.location.origin);
                                // only one will succeed
                                origins.forEach(origin => window.opener.postMessage({msg_data}, origin));
                                window.close();
                            </script>
                        </head>
                        <body></body>
                    </html>'''.format(msg_data=json.dumps(resp_obj), origins=json.dumps(origins)),
                resp_code
            )
            response.headers['Content-Type'] = 'text/html'
            return response
        else:
            return jsonify(**resp_obj), resp_code

    authn_response = saml_client().parse_authn_request_response(
        request.form['SAMLResponse'],
        saml2.entity.BINDING_HTTP_POST
    )
    identity = authn_response.get_identity()
    email = identity['emailAddress'][0]
    domain = email.split('@')[1]
    name = (current_app.config.get('SAML2_USER_NAME_FORMAT', '{givenName} {surname}')).format(**dict(map(lambda x: (x[0], x[1][0]), identity.items())))

    groups = identity.get('groups', [])
    if is_authorized('ALLOWED_SAML2_GROUPS', groups):
        return _make_response({'status': 'error', 'message': 'User {} is not authorized'.format(email)}, 403)

    customer = get_customer(email, groups=[domain])

    token = create_token(email, name, email, provider='saml2', customer=customer, groups=groups)
    return _make_response({'status': 'ok', 'token': token.tokenize}, 200)


@auth.route('/auth/saml/metadata.xml', methods=['GET'])
def saml_metadata():
    edesc = saml2.metadata.entity_descriptor(spConfig())
    response = make_response(str(edesc))
    response.headers['Content-Type'] = 'text/xml; charset=utf-8'
    return response
