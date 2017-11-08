import os
import pickle
import hashlib
import logging

from . import ExplorationTechnique


l = logging.getLogger("angr.exploration_techniques.cacher")


class Cacher(ExplorationTechnique):
    """
    An exploration technique that caches states during symbolic execution.
    """

    def __init__(self, when=None, dump_cache=True, load_cache=True, cache_file=None, dump_func=None, load_func=None):
        """
        :param dump_cache: Whether to dump data to cache.
        :param load_cache: Whether to load data from cache.
        :param cache_file: File name format string to cache data.
        :param when      : If provided, should be a function that takes a SimulationManager and returns
                           a Boolean, or the address of the state to be cached.
        :param dump_func : If provided, should be a function that defines how Cacher should cache the
                           SimulationManager. Default to caching the active stash.
        :param load_func : If provided, should be a function that defines how Cacher should uncache the
                           SimulationManager. Default to uncaching the stash to be stepped.
        """

        super(Cacher, self).__init__()
        self._dump_cond = self._condition_to_lambda(when)
        self._dump_cache = dump_cache
        self._load_cache = load_cache
        self._cache_file = cache_file
        self._dump_func = self._dump_stash if dump_func is None else dump_func
        self._load_func = self._load_stash if load_func is None else load_func

    def setup(self, simgr):
        self.project = simgr._project
        binary = self.project.filename
        binhash = hashlib.md5(open(binary).read()).hexdigest()

        if self._cache_file is None:
            self._cache_file = os.path.join("/tmp", "%s-%s.cache" % (os.path.basename(binary), binhash))
        else:
            try:
                self._cache_file = self._cache_file % {'name': os.path.basename(binary), 'binhash': binhash, 'addr': '%(addr)s'}
            except KeyError:
                l.error("Only the following cache keys are accepted: 'name', 'binhash' and 'addr'.")
                raise

        if self._load_cache and os.path.exists(self._cache_file):
            l.warning("Loading from %s...", self._cache_file)

            with open(self._cache_file) as f:
                self._load_func(f, simgr)

    def step(self, simgr, stash, **kwargs):
        # We cache if any of the states in 'stash' satisfies the condition.
        for s in simgr.stashes[stash]:
            if self._dump_cache and self._dump_cond(s):
                self._cache_file = self._cache_file % {'addr': hex(s.addr)[:-1]}
                if os.path.exists(self._cache_file):
                    continue
                l.warning("Caching to %s...", self._cache_file)

                f = open(self._cache_file, 'wb')
                try:
                    self._dump_func(f, simgr, stash)
                finally:
                    if f:
                        f.close()

        return simgr.step(stash=stash, **kwargs)

    @staticmethod
    def _load_stash(f, simgr):
        stash = pickle.load(f)
        for s in stash:
            s.project = simgr._project
        simgr.stashes['active'] = stash

    @staticmethod
    def _dump_stash(f, simgr, stash):
        # Do not pickle project
        for s in simgr.stashes[stash]:
            s.project = None
            s.history.trim()
        try:
            pickle.dump(simgr.stashes[stash], f, pickle.HIGHEST_PROTOCOL)
        except RuntimeError as e: # maximum recursion depth can be reached here
            l.error("Unable to cache, '%s' during pickling", e.message)
        finally:
            for s in simgr.stashes[stash]:
                s.project = simgr._project