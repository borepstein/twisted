import os.path, base64
from twisted.internet import defer
from common import NS, getNS, MP
import keys, transport, service

class SSHUserAuthServer(service.SSHService):
    name = 'ssh-userauth'
    protocolMessages = None # set later
    supportedAuthentications = ('publickey','password')
    authenticatedWith = []

    def tryAuth(self, kind, user, data):
        print 'trying auth %s for %s' % (kind, user)
        #print 'with data: %s' % repr(data)
        f= getattr(self,'auth_%s'%kind, None)
        if f:
            return f(user, data)
        return 0

    def ssh_USERAUTH_REQUEST(self, packet):
        user, nextService, method, rest = getNS(packet, 3)
        self.nextService = nextService
        r = self.tryAuth(method, user, rest)
        if r<0: # sent a different packet type back
            return
        if type(r) != type(defer.Deferred()):
            if r:
                r = defer.succeed(None)
            else:
                r = defer.fail(None)
        r.addCallbacks(self._cbGoodAuth, self._cbBadAuth, callbackArgs = (method,))

    def _cbGoodAuth(self, foo, method):
        self.authenticatedWith.append(method)
        if self.areDone():
            self.transport.sendPacket(MSG_USERAUTH_SUCCESS, '')
            self.transport.setService(self.transport.factory.services[self.nextService]())
        else:
            self.transport.sendPacket(MSG_USERAUTH_FAILURE, NS(','.join(self.supportedAuthentications))+'\xff')

    def _cbBadAuth(self, foo):
        self.transport.sendPacket(MSG_USERAUTH_FAILURE, NS(','.join(self.supportedAuthentications))+'\x00')

    def auth_publickey(self, user, packet):
        hasSig = ord(packet[0])
        self.hasSigType = hasSig # protocol impl.s differ in this
        algName, blob, rest = getNS(packet[1:], 2)
        print hasSig, algName
        if hasSig:
            if self.isValidKeyFor(user, blob) and self.verifySignatureFor(user, blob, getNS(rest)[0]):
                    return 1
            return 0
        else:
            if self.isValidKeyFor(user, blob):
                self.transport.sendPacket(MSG_USERAUTH_PK_OK, packet[1:])
                return -1
            return 0

    def verifySignatureFor(self, user, blob, signature):
        pubKey = keys.getPublicKeyObject(data = blob)
        b = NS(self.transport.sessionID) + chr(MSG_USERAUTH_REQUEST) + \
            NS(user) + NS(self.nextService) + NS('publickey') + chr(self.hasSigType) + \
            NS(keys.objectType(pubKey)) + NS(blob)
        return keys.pkcs1Verify(pubKey, signature, b)

#    def aut

    # overwrite on the client side            
    def areDone(self):
        return len(self.authenticatedWith)>0
        
    def isValidKeyFor(self, user, pubKey):
        home = os.path.expanduser('~%s/.ssh/' % user)
        for file in ['authorized_keys', 'authorized_keys2']:
            if os.path.exists(home+file):
                lines = open(home+file).readlines()
                for l in lines:
                    if base64.decodestring(l.split()[1])==pubKey:
                        return 1
        print 'not vaild key'
        return 0

class SSHUserAuthClient(service.SSHService):
    name = 'ssh-userauth'
    protocolMessages = None # set later
    def __init__(self, user, nextService, cls):
        self.user = user
        self.nextService = nextService
        self.cls = cls
        self.triedPublicKeys = []

    def serviceStarted(self):
        self.askForAuth('none', '')

    def askForAuth(self, kind, extraData):
        self.lastAuth = kind
        self.transport.sendPacket(MSG_USERAUTH_REQUEST, NS(self.user) + \
                                  NS(self.nextService) + NS(kind) + extraData)
    def tryAuth(self, kind):
        f= getattr(self,'auth_%s'%kind, None)
        if f:
            return f()
        
    def ssh_USERAUTH_SUCCESS(self, packet):
        self.transport.setService = self.nextService
        self.nextService.transport = self.transport
        self.nextService.serviceStarted()

    def ssh_USERAUTH_FAILURE(self, packet):
        canContinue, partial = getNS(packet)
        canContinue = canContinue.split(',')
        print canContinue
        partial = ord(partial)
        for method in canContinue:
            if method not in self.authenticatedWith and self.tryAuth(method):
                break

    def ssh_USERAUTH_PK_OK(self, packet):
        if self.lastAuth == 'publickey':
            # this is ok
            privateKey = keys.getPrivateKeyObject(os.path.expanduser('~/.ssh/id_rsa'))
            publicKey = keys.getPublicKeyString(os.path.expanduser('~/.ssh/id_rsa.pub'))
            b = NS(self.transport.sessionID) + chr(MSG_USERAUTH_REQUEST) + \
                NS(self.user) + NS(self.nextService) + NS('publickey') + '\xff' + \
                NS('ssh-rsa') + NS(publicKey)
            self.askForAuth('publickey', '\xff' + NS('ssh-rsa') + NS(publicKey) + \
                            NS(keys.pkcs1Sign(privateKey, b)))
        elif self.lastAuth == 'password':
            prompt, language, rest = getNS(packet, 2)
            op = getpass('Old Password: ')
            np = getpass(prompt)
            self.askForAuth('password', '\xff'+NS(op)+NS(np))

    def auth_publickey(self):
        if os.path.exists(os.path.expanduser('~/.ssh/id_rsa')) and not 'file' in self.triedPublicKeys:
            self.triedPublicKeys.append('file')
            self.askForAuth('publickey', '\x00' + NS('ssh-rsa') + \
                            NS(keys.getPublicKeyString(os.path.expanduser('~/.ssh/id_rsa.pub'))))
            return 1

    def auth_password(self):
        d = self.getPassword()
        d.addCallback(self._cbPassword)
        return 1

    def _cbPassword(self, password):
        self.askForAuth('password', '\x00'+NS(password))

    def getPassword(self, prompt = None):
        if not prompt:
            prompt = 'Password for %s: ' % self.user
        return d.succeed(getpass(prompt))

def getpass(prompt = "Password: "):
    import termios, sys
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] = new[3] & ~termios.ECHO          # lflags
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        passwd = raw_input(prompt)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return passwd

MSG_USERAUTH_REQUEST          = 50
MSG_USERAUTH_FAILURE          = 51
MSG_USERAUTH_SUCCESS          = 52
MSG_USERAUTH_BANNER           = 53
MSG_USERAUTH_PASSWD_CHANGEREQ = 60
MSG_USERAUTH_PK_OK            = 60

messages = {}
import userauth
for v in dir(userauth):
    if v[:4]=='MSG_':
        messages[getattr(userauth,v)] = v # doesn't handle doubles

SSHUserAuthServer.protocolMessages = messages
SSHUserAuthClient.protocolMessages = messages
