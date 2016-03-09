#!/usr/bin/env python

import os
import sys
import types


DAZEL_RC_FILE = ".dazelrc"
DAZEL_RUN_FILE = ".dazel_run"

DEFAULT_INSTANCE_NAME = "dazel"
DEFAULT_IMAGE_NAME = "dazel"
DEFAULT_LOCAL_DOCKERFILE = "Dockerfile.dazel"
DEFAULT_REMOTE_RPOSITORY = "dazel"
DEFAULT_DIRECTORY = os.getcwd()
DEFAULT_COMMAND = "/bazel/output/bazel"
DEFAULT_VOLUMES = []
DEFAULT_BAZEL_USER_OUTPUT_ROOT = "%s/.cache/bazel" % os.environ.get("HOME", "~")


class DockerInstance:
    """Manages communication and runs commands on associated docker container.

    A DockerInstance can build the image for the container if necessary, run it,
    set it up through configuration variables, and pass on commands to it.
    It streams the output directly and blocks until the command finishes.
    """
    
    def __init__(self, instance_name, image_name, dockerfile, repository,
                       directory, command, volumes, bazel_user_output_root,
                       dazel_run_file):
        self.instance_name = instance_name
        self.image_name = image_name
        self.dockerfile = dockerfile
        self.repository = repository
        self.directory = directory
        self.command = command
        self.bazel_user_output_root = bazel_user_output_root
        self.dazel_run_file = dazel_run_file

        self._add_volumes(volumes)
        
    @classmethod
    def from_config(cls):
        config = cls._config_from_file()
        config.update(cls._config_from_environment())
        return DockerInstance(
                instance_name=config.get("DAZEL_INSTANCE_NAME", DEFAULT_INSTANCE_NAME),
                image_name=config.get("DAZEL_IMAGE_NAME", DEFAULT_IMAGE_NAME),
                dockerfile=config.get("DAZEL_DOCKERFILE", DEFAULT_LOCAL_DOCKERFILE),
                repository=config.get("DAZEL_REPOSITORY", DEFAULT_REMOTE_RPOSITORY),
                directory=config.get("DAZEL_DIRECTORY", DEFAULT_DIRECTORY),
                command=config.get("DAZEL_COMMAND", DEFAULT_COMMAND),
                volumes=config.get("DAZEL_VOLUMES", DEFAULT_VOLUMES),
                bazel_user_output_root=config.get("DAZEL_BAZEL_USER_OUTPUT_ROOT",
                                                  DEFAULT_BAZEL_USER_OUTPUT_ROOT),
                dazel_run_file=config.get("DAZEL_RUN_FILE", DAZEL_RUN_FILE))

    def send_command(self, args):
        command = "docker exec -it %s %s --output_user_root=%s %s" % (
            self.instance_name, self.command, self.bazel_user_output_root,
            '"%s"' % '" "'.join(args))
        return os.system(command)

    def start(self):
        """Starts the dazel docker container."""
        # Build or pull the relevant dazel image.
        if os.path.exists(self.dockerfile):
            rc = self._build()
        else:
            rc = self._pull()
            # If we have the image, don't stop everything just because we
            # couldn't pull.
            if rc and self._image_exists():
                rc = 0

        # Handle image creation errors.
        if rc:
            return rc

        # Run the container itself.
        print ("Starting docker container '%s'..." % self.instance_name)
        command = "docker stop %s >& /dev/null ; " % (self.instance_name)
        command += "docker rm %s >& /dev/null ; " % (self.instance_name)
        command += "docker run -id --name=%s -w %s %s %s/%s /bin/bash" % (
            self.instance_name, os.path.realpath(self.directory),
            self.volumes, self.repository, self.image_name)
        rc = os.system(command)
        if rc:
            return rc

        # Touch the dazel run file to change the timestamp.
        file(self.dazel_run_file, "w").write(self.instance_name + "\n")
        print ("Done.")

        return rc

    def is_running(self):
        """Checks if the container is currently running."""
        command = "docker ps | grep %s >& /dev/null" % (self.instance_name)
        rc = os.system(command)
        return (rc == 0)

    def _image_exists(self):
        """Checks if the dazel image exists in the local repository."""
        command = "docker images | grep %s/%s >& /dev/null" % (
            self.repository, self.image_name)
        rc = os.system(command)
        return (rc == 0)

    def _build(self):
        """Builds the dazel image from the local dockerfile."""
        if not os.path.exists(self.dockerfile):
            raise RuntimeError("No Dockerfile to build the dazel image from.")

        command = "docker build -t %s/%s -f %s %s" % (
            self.repository, self.image_name, self.dockerfile, self.directory)
        return os.system(command)

    def _pull(self):
        """Pulls the relevant image from the dockerhub repository."""
        if not self.repository:
            raise RuntimeError("No repository to pull the dazel image from.")

        command = "docker pull %s/%s" % (self.repository, self.image_name)
        return os.system(command)

    def _add_volumes(self, volumes):
        """Add the given volumes to the run string, and the bazel volumes we need anyway."""
        # DAZEL_VOLUMES can be a python iterable or a comma-separated string.
        if isinstance(volumes, str):
            volumes = [v.strip() for v in volumes.split(",")]
        elif volumes and not isinstance(volumes, types.Iterable):
            raise RuntimeError("DAZEL_VOLUMES must be comma-separated string "
                               "or python iterable of strings")

        # Find the real source and output directories.
        real_directory = os.path.realpath(self.directory)
        real_bazelout = os.path.realpath(
            os.path.join(self.directory, "bazel-out", ".."))
        volumes += [
            "%s:%s" % (real_directory, real_directory),
            "%s:%s" % (real_bazelout, real_bazelout),
        ]
        self.volumes = '-v "%s"' % '" -v "'.join(volumes)

        # If the user hasn't explicitly set a DAZEL_BAZEL_USER_OUTPUT_ROOT for
        # bazel, set it from the output directory so that we get the build
        # results on the host.
        if not self.bazel_user_output_root and "/_bazel" in real_bazelout:
            parts = real_bazelout.split("/_bazel")
            first_part = parts[0]
            second_part = "/_bazel" + parts[1].split("/")[0]
            self.bazel_user_output_root = first_part + second_part

        # Make sure the path exists on the host.
        if not os.path.isdir(self.bazel_user_output_root):
            os.makedirs(self.bazel_user_output_root)

    @classmethod
    def _config_from_file(cls):
        """Creates a configuration from a .dazelrc file."""
        directory = os.environ.get("DAZEL_DIRECTORY", DEFAULT_DIRECTORY)
        local_dazelrc_path = os.path.join(directory, DAZEL_RC_FILE)
        dazelrc_path = os.environ.get("DAZEL_RC_FILE", local_dazelrc_path)

        if not os.path.exists(dazelrc_path):
            return {}

        config = {}
        with open(dazelrc_path, "r") as dazelrc:
            exec(dazelrc.read(), config)
        return config

    @classmethod
    def _config_from_environment(cls):
        """Creates a configuration from environment variables."""
        return { name: value
                 for (name, value) in os.environ.items()
                 if name.startswith("DAZEL_") }


def main():
    # Read the configuration either from .dazelrc or from the environment.
    di = DockerInstance.from_config()

    # If there is no .dazel_run file, or it is too old, start the DockerInstance.
    if (not os.path.exists(di.dazel_run_file) or
        not di.is_running() or
        (os.path.exists(di.dockerfile) and
         os.path.getctime(di.dockerfile) > os.path.getctime(di.dazel_run_file))):
        rc = di.start()
        if rc:
            return rc

    # Forward the command line arguments to the container.
    return di.send_command(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

