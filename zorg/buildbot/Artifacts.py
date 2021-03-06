import buildbot
import config

from buildbot.steps.shell import WithProperties
from zorg.buildbot.PhasedBuilderUtils import setProperty, determine_phase_id
from zorg.buildbot.PhasedBuilderUtils import set_config_option

# Get some parameters about where to upload and download results from.
# Set up defaults assuming we aren't in  production mode which
# assume that we are just using a local user.
import getpass
rsync_user = getpass.getuser()
master_name = 'localhost'
master_protocol = 'http'
base_download_url = 'http://%s/~%s/artifacts' % (master_name, rsync_user)
package_url = 'http://%s/~%s/packages' % (master_name, rsync_user)
base_rsync_path = '%s@%s:~/artifacts' % (rsync_user , master_name)
curl_flags = '-svLo'

is_production = set_config_option('Master Options', 'is_production')
if is_production:
    rsync_user = set_config_option('Master Options', 'rsync_user',
                                   'buildmaster')
    master_name = set_config_option('Master Options', 'master_name',
                                    'localhost')
    master_protocol = set_config_option('Master Options', 
                                        'master_protocol', 'http')
    base_download_url = '%s://%s/artifacts' % (master_protocol, master_name)
    base_package_url = '%s://%s/packages' % (master_protocol, master_name)
    package_url = set_config_option('Master Options', 'package_url',
                                    base_package_url)
    base_rsync_path = set_config_option('Master Options', 'base_rsync_path',
                                        '%s@%s:~/artifacts' %
                                        (rsync_user , master_name))
    master_name = set_config_option('Master Options', 'curl_flags',
                                    '-svLo')

# This method is used in determining the name of a given compiler archive
def _determine_compiler_kind(props):
    # we need to differentiate between configure/make style builds (clang)
    # from buildit style builde (apple-clang)

    buildName = props['buildername']
    kind = buildName
    subname = buildName
    if '_' in buildName:
        kind,subname = buildName.split('_', 1)
    if 'clang' in kind:
        subname = kind
    for kind in ('apple-clang','clang'):
        if kind in subname:
            return kind
    raise ValueError, "unknown compiler"

# compiler_path and archive_name should be completely deterministic. Any 
# methods acting on an archive should use the following two methods to
# calculate the path and/or name for an archive
def _determine_archive_name(props):
    # phase_id must be set upstream. Usually by a phase builder
    archive_name = _determine_compiler_kind(props)
    if props.has_key('phase_id') and props['phase_id']:
        archive_name += '-' + props['phase_id'] + '.tar.gz'
    else:
        raise ValueError, "phase_id doesn't exist"
    return archive_name
    
def _determine_compiler_path(props):
    # We need to segregate compiler builds based on both branch and builder
    # TODO: better solution when branch is None
    compiler_path = props['buildername']
    if props.has_key('default_branch') and props['default_branch']:
        compiler_path = props['default_branch']
    elif props.has_key('branch') and props['branch']:
        compiler_path = props['branch']
    elif props.has_key('use_builder') and props['use_builder']:
        compiler_path = props['use_builder']
    return compiler_path

def _determine_bootstrap_url(props):
    if props.has_key('scheduler'):
        name= ''
        if props['scheduler'].startswith('phase2'):
            # always use phase1 compiler for phase2
            # TODO: this shouldn't be hard coded
            name = 'clang-x86_64-darwin11-nobootstrap-RA'
        else:
            # always use phase2 compiler for phase3 & phase4 compiler builds
            # TODO: this shouldn't be hard coded
            name = 'clang-x86_64-darwin11-RA'
        curl = base_download_url + '/' + name + '/clang-' + props['phase_id']
        curl += '.tar.gz'
        return curl
    else:
        # if we get this far, we can assume that someone clicked 'rebuild'
        # (otherwise it would have a scheduler, or not have a phase_id)
        # we'll fall back to the phase1 build for this compiler
        curl = download_url + '/clang-x86_64-darwin11-nobootstrap-RA/'
        curl += props['buildername'] + '.tar.gz'
        return curl

def GetCompilerRoot(f):
    # The following steps are used to retrieve a compiler archive
    # clean out any existing archives
    f.addStep(buildbot.steps.shell.ShellCommand(
            name='rm.host-compiler',
            command=['rm', '-rfv', 'host-compiler', 'host-compiler.tar.gz'],
            haltOnFailure=False, description=['rm', 'host-compiler'],
            workdir=WithProperties('%(builddir)s')))
    setProperty(f, 'rootURL', 
                WithProperties( base_download_url + '/%(getpath)s/%(getname)s',
                               getpath=_determine_compiler_path,
                               getname=_determine_archive_name))
    # curl down the archive
    f.addStep(buildbot.steps.shell.ShellCommand(
              name='download.artifacts',
              command=['curl', curl_flags, 'host-compiler.tar.gz',
                       WithProperties('%(rootURL)s')],
              haltOnFailure=True,
              description=['download build artifacts'],
              workdir=WithProperties('%(builddir)s')))
    # extract the compiler root from the archive
    f.addStep(buildbot.steps.shell.ShellCommand(
              name='unzip', command=['tar', '-zxvf','../host-compiler.tar.gz'],
              haltOnFailure=True, description=['extract', 'host-compiler'],
              workdir='host-compiler'))
    return f

def uploadArtifacts(f, rootdir='clang-install'):
    #phase_id is required to make sure that path to archives are deterministic.
    setProperty(f, 'phase_id', WithProperties('%(get_phase_id)s',
                get_phase_id = determine_phase_id))
    # we always create/name a compiler archive based on the same criteria
    archive_path = WithProperties('%(builddir)s/%(getname)s',
                                  getname=_determine_archive_name)
    if rootdir.endswith('install'):
        cit_path = 'clang-build/**/bin/c-index-test'
        copy_command = 'cp %s %s/bin/' % (cit_path, rootdir)
        f.addStep(buildbot.steps.shell.ShellCommand(
                  name='add.cit', haltOnFailure=True,
                  command = ['sh', '-c', copy_command],
                  description=['add c-index-test to root'],
                  workdir=WithProperties('%(builddir)s')))
    f.addStep(buildbot.steps.shell.ShellCommand(
              name='tar.and.zip', haltOnFailure=True,
              command=['tar', 'czvf', archive_path, './'],
              description=['tar', '&', 'zip'], workdir=rootdir))
    # Upload the archive.
    archive_dest = WithProperties(base_rsync_path +'/%(getpath)s/',
                                  getpath=_determine_compiler_path)
    f.addStep(buildbot.steps.shell.ShellCommand(
              name='upload.artifacts', haltOnFailure=True,
              command=['rsync', '-pave', 'ssh', archive_path, archive_dest],
              description=['upload build artifacts'],
              workdir=WithProperties('%(builddir)s')))
    # Set the artifact URL in a property for easy access from the build log.
    download_str = base_download_url + '/%(getpath)s/%(getname)s'
    artifactsURL = WithProperties(download_str, getpath=_determine_compiler_path,
                                  getname=_determine_archive_name)
    setProperty(f, 'artifactsURL', artifactsURL)
    return f

def determine_url(props):
    if props.has_key('phase_id') and props.has_key('category'):
        if props['category'].startswith('build-'):
            return _determine_bootstrap_url(props)
        project = project_from_name(props['buildername'])
        name = props['use_builder']
        curl = download_url + '/' + name + '/' + project_from_name(name)
        curl += '-' + props['phase_id'] + '.tar.gz'
        return curl
    # phase_id does not exist, so this has to be a manually triggered build.
    # we will fall back to the latest_validated build for the use_builder
    # property if it exists, otherwise, fall back to the latest_validated build
    # for this builder.
    curl = download_url + '/latest_validated/'
    if props.has_key('use_builder'):
        curl += props['use_builder'] + '.tar.gz'
    else:
        curl += props['buildername'] + '.tar.gz'
    return curl

def GetCompilerArtifacts(f):
    f.addStep(buildbot.steps.shell.ShellCommand(
            name='rm.host-compiler',
            command=['rm', '-rfv', 'host-compiler', 'host-compiler.tar.gz'],
            haltOnFailure=False, description=['rm', 'host-compiler'],
            workdir=WithProperties('%(builddir)s')))
    f.addStep(buildbot.steps.shell.ShellCommand(
              name='download.artifacts',
              command=['curl', '-svo', 'host-compiler.tar.gz',
                       WithProperties('%(get_curl)s', get_curl=determine_url)],
              haltOnFailure=True, description=['download build artifacts'],
              workdir=WithProperties('%(builddir)s')))
    f.addStep(buildbot.steps.shell.ShellCommand(
              name='unzip', command=['tar', '-zxvf','../host-compiler.tar.gz'],
              haltOnFailure=True, description=['extract', 'host-compiler'],
              workdir='host-compiler'))
    return f
