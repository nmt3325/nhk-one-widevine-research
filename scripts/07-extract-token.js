// Extract the current main access token from a running NHK ONE app.
// Usage: frida -U -p <pid> --runtime=v8 -l 07-extract-token.js
// Or:    frida -U -f nhk.app.tep --runtime=v8 -l 07-extract-token.js
Java.perform(function () {
    Java.choose('com.sunagalab.nhkbxclient.appstate.UserAuthManager', {
        onMatch: function (instance) {
            try {
                var headerVal = instance.getCurrentMainAccessTokenHeaderValue();
                send({ kind: 'TOKEN', value: headerVal });
            } catch (e) {
                try {
                    var token = instance.getCurrentMainAccessToken();
                    send({ kind: 'TOKEN', value: 'Bearer ' + token });
                } catch (e2) {
                    send({ kind: 'ERROR', value: 'token read failed: ' + e2 });
                }
            }
        },
        onComplete: function () {}
    });
});
