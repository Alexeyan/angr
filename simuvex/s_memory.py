#!/usr/bin/env python

import logging
import itertools
import cooldict

l = logging.getLogger("s_memory")

import symexec
import s_exception

addr_mem_counter = itertools.count()
var_mem_counter = itertools.count()
merge_mem_counter = itertools.count()
# Conventions used:
# 1) The whole memory is readable
# 2) Memory locations are by default writable
# 3) Memory locations are by default not executable

class SimMemoryError(s_exception.SimError):
	pass

class Vectorizer(cooldict.CachedDict):
	def __init__(self, backer):
		super(Vectorizer, self).__init__(backer)
		self.cooldict_ignore = True

	def default_cacher(self, k):
		b = self.backer[k]
		if type(b) in ( int, str ):
			b = symexec.BitVecVal(ord(self.backer[k]), 8)

		self.cache[k] = b
		return b

class SimMemory(object):
	#__slots__ = [ 'mem', 'limit', 'bits', 'max_mem', 'id' ]

	def __init__(self, backer=None, bits=64, memory_id="mem"):
		if backer is None:
			backer = cooldict.BranchingDict()

		if not isinstance(backer, cooldict.BranchingDict):
			backer = cooldict.BranchingDict(backer)

		self.mem = backer
		self.limit = 1024
		self.bits = bits
		self.max_mem = 2**self.bits
		self.id = memory_id

	def read_from(self, addr, num_bytes):
		buff = [ ]
		for i in range(0, num_bytes):
			try:
				buff.append(self.mem[addr+i])
			except KeyError:
				mem_id = "%s_%x_%d" % (self.id, addr+i, var_mem_counter.next())
				l.debug("Creating new symbolic memory byte %s", mem_id)
				b = symexec.BitVec(mem_id, 8)
				self.mem[addr+i] = b
				buff.append(b)

		if len(buff) == 1:
			return buff[0]
		else:
			return symexec.Concat(*buff)

	def write_to(self, addr, cnt):
		for off in range(0, cnt.size(), 8):
			target = addr + off/8
			new_content = symexec.Extract(cnt.size() - off - 1, cnt.size() - off - 8, cnt)
			self.mem[target] = new_content

	def concretize_addr(self, v, strategies):
		if v.is_symbolic() and not v.satisfiable():
			raise SimMemoryError("Trying to concretize with unsat constraints.")

		# if there's only one option, let's do it
		if v.is_unique():
			return [ v.any() ]

		for s in strategies:
			if s == "free":
				# TODO
				pass
			if s == "allocated":
				pass
			if s == "writeable":
				# TODO
				pass
			if s == "executable":
				# TODO
				pass
			if s == "symbolic":
				# if the address concretizes to less than the threshold of values, try to keep it symbolic
				if v.max() - v.min() < self.limit:
					return v.any_n(self.limit)
			if s == "any":
				return [ v.any() ]

		raise SimMemoryError("Unable to concretize address with the provided strategies.")

	def concretize_write_addr(self, dst):
		return self.concretize_addr(dst, strategies = [ "free", "writeable", "any" ])

	def concretize_read_addr(self, dst):
		return self.concretize_addr(dst, strategies=['symbolic', 'any'])

	def __contains__(self, dst):
		if type(dst) in (int, long):
			addr = dst
		elif dst.is_symbolic():
			try:
				addr = self.concretize_addr(dst, strategies=['allocated'])[0]
			except SimMemoryError:
				return False
		else:
			addr = dst.any()

		return addr in self.mem

	def store(self, dst, cnt):
		if type(dst) in (int, long):
			addr = dst
			constraint = [ ]
		elif dst.is_unique():
			addr = dst.any()
			constraint = [ ]
		else:
			addr = self.concretize_write_addr(dst)[0]
			constraint = [ dst.expr == addr ]

		self.write_to(addr, cnt)
		return constraint

	def load(self, dst, size):
		if type(dst) in (int, long):
			return self.read_from(dst, size), [ ]

		if dst.is_unique():
			return self.read_from(dst.any(), size), [ ]

		# otherwise, get a concrete set of read addresses
		addrs = self.concretize_read_addr(dst)

		# if there's a single address, it's easy
		if len(addrs) == 1:
			return self.read_from(addrs[0], size), [ dst.expr == addrs[0] ]

		# otherwise, create a new symbolic variable and return the mess of constraints and values
		m = symexec.BitVec("%s_addr_%s" %(self.id, addr_mem_counter.next()), size*8)
		e = symexec.Or(*[ symexec.And(m == self.read_from(addr, size), dst.expr == addr) for addr in addrs ])
		return m, [ e ]

	# Return a copy of the SimMemory
	def copy(self):
		#l.debug("Copying %d bytes of memory with id %s." % (len(self.mem), self.id))
		c = SimMemory(self.mem.branch(), bits=self.bits, memory_id=self.id)
		return c

	# Gets the set of changed bytes between self and other.
	def changed_bytes(self, other):
		common_ancestor = self.mem.common_ancestor(other.mem)
		if common_ancestor == None:
			l.warning("Merging without a common ancestor. This will be very slow.")
			our_changes, our_deletions = set(self.mem.keys()), set()
			their_changes, their_deletions = set(other.mem.keys()), set()
		else:
			our_changes, our_deletions = self.mem.changes_since(common_ancestor)
			their_changes, their_deletions = other.mem.changes_since(common_ancestor)

		#both_changed = our_changes & their_changes
		#ours_changed_only = our_changes - both_changed
		#theirs_changed_only = their_changes - both_changed
		#both_deleted = their_deletions & our_deletions
		#ours_deleted_only = our_deletions - both_deleted
		#theirs_deleted_only = their_deletions - both_deleted

		return our_changes | our_deletions | their_changes | their_deletions

	# Unconstrain a byte
	def unconstrain_byte(self, addr):
		unconstrained_byte = symexec.BitVec("%s_unconstrain_0x%x_%s" % (self.id, addr, addr_mem_counter.next()), 8)
		self.store(addr, unconstrained_byte)


	# Replaces the differences between self and other with unconstrained bytes.
	def unconstrain_differences(self, other):
		changed_bytes = self.changed_bytes(other)
		l.debug("Will unconstrain %d %s bytes", len(changed_bytes), self.id)
		for b in changed_bytes:
			self.unconstrain_byte(b)

	# Merge this SimMemory with the other SimMemory
	def merge(self, others, flag, flag_values):
		changed_bytes = set()
		for o in others:
			changed_bytes |= self.changed_bytes(o)

		constraints = [ ]
		for addr in changed_bytes:
			# NOTE: This assumes that loading a concrete addr can't create new constraints.
			#		This is true now, but who knows if it'll be true in the future.
			alternatives = [ self.load(addr, 1)[0] ]
			for o in others:
				alternatives.append(o.load(addr, 1)[0])

			and_constraints = [ ]
			merged_val = symexec.BitVec("%s_merge_0x%x_%s" % (self.id, addr, merge_mem_counter.next()), 8)
			for a, fv in zip(alternatives, flag_values):
				and_constraints.append(symexec.And(flag == fv, merged_val == a))
			self.store(addr, merged_val)

			constraints.append(symexec.Or(*and_constraints))
		return constraints
