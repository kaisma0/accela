from pathlib import Path


class Paths:
    # Go up two parents: utils/ -> src/ -> project root
    BASE_DIR = Path(__file__).parent.parent.resolve()
    RES = BASE_DIR / "res"
    DEPS = BASE_DIR / "deps"

    @classmethod
    def deps(cls, relative_path=None):
        """Grabs from the dependencies folder by relative name.
        If no relative path is specified, it returns the /deps/ folder."""
        return cls.DEPS / relative_path if relative_path is not None else cls.DEPS

    @classmethod
    def resource(cls, relative_path=None):
        """Grabs a resource by relative name.
        If no relative path is specified, it returns the /res/ folder."""
        return cls.RES / relative_path if relative_path is not None else cls.RES

    @classmethod
    def base(cls, relative_path=None):
        """Grabs a resource from the base path.
        If no relative path is specified, it returns the base path."""
        return (
            cls.BASE_DIR / relative_path if relative_path is not None else cls.BASE_DIR
        )

    @classmethod
    def absolute(cls, path):
        """Return the absolute, expanded path as a Path object."""
        return Path(path).expanduser().resolve()

    @classmethod
    def sound_path(cls, filename):
        """For use with audio_manager.
        Returns the sound file path from the res/ folder.
        """
        return Paths.resource(filename)
