#!/usr/bin/env python

# Copyright (c) 2017, DIANA-HEP
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os
import math
import struct
import sys

import numpy

import oamap.generator

if sys.version_info[0] > 2:
    xrange = range

class Fillable(object):
    def __init__(self, dtype, dims):
        raise NotImplementedError
    def append(self, value):
        raise NotImplementedError
    def extend(self, values):
        raise NotImplementedError
    def flush(self):
        pass
    def __len__(self):
        raise NotImplementedError
    def __getitem__(self, index):
        raise NotImplementedError
    def close(self):
        pass

################################################################ make fillables

def _makefillables(generator, fillables, makefillable, liststarts, unionoffsets):
    if isinstance(generator, oamap.generator.Masked):
        fillables[generator.mask] = makefillable(generator.mask, numpy.bool_, ())

    if isinstance(generator, oamap.generator.PrimitiveGenerator):
        if generator.dtype is None:
            raise ValueError("dtype is unknown (None) for Primitive generator at {0}".format(repr(generator.data)))
        if generator.dims is None:
            raise ValueError("dims is unknown (None) for Primitive generator at {0}".format(repr(generator.data)))
        fillables[generator.data] = makefillable(generator.data, generator.dtype, generator.dims)

    elif isinstance(generator, oamap.generator.ListGenerator):
        if liststarts:
            fillables[generator.starts] = makefillable(generator.starts, generator.dtype, ())
        fillables[generator.stops] = makefillable(generator.stops, generator.dtype, ())
        _makefillables(generator.content, fillables, makefillable, liststarts, unionoffsets)

    elif isinstance(generator, oamap.generator.UnionGenerator):
        fillables[generator.tags] = makefillable(generator.tags, generator.dtype, ())
        if unionoffsets:
            fillables[generator.offsets] = makefillable(generator.offsets, generator.dtype, ())
        for possibility in generator.possibilities:
            _makefillables(possibility, fillables, makefillable, liststarts, unionoffsets)

    elif isinstance(generator, oamap.generator.RecordGenerator):
        for field in generator.fields.values():
            _makefillables(field, fillables, makefillable, liststarts, unionoffsets)

    elif isinstance(generator, oamap.generator.TupleGenerator):
        for field in generator.types:
            _makefillables(field, fillables, makefillable, liststarts, unionoffsets)

    elif isinstance(generator, oamap.generator.PointerGenerator):
        fillables[generator.positions] = makefillable(generator.positions, generator.dtype, ())
        if not generator._internal:
            _makefillables(generator.target, fillables, makefillable, liststarts, unionoffsets)

    else:
        raise AssertionError("unrecognized generator type: {0}".format(generator))

def fillablelists(generator, liststarts=False, unionoffsets=False):
    if not isinstance(generator, oamap.generator.Generator):
        generator = generator.generator()
    fillables = {}
    _makefillables(generator, fillables, lambda name, dtype, dims: FillableList(dtype, dims=dims), liststarts, unionoffsets)
    return fillables

def fillablearrays(generator, liststarts=False, unionoffsets=False, chunksize=8192):
    if not isinstance(generator, oamap.generator.Generator):
        generator = generator.generator()
    fillables = {}
    _makefillables(generator, fillables, lambda name, dtype, dims: FillableArray(dtype, dims=dims, chunksize=chunksize), liststarts, unionoffsets)
    return fillables

def fillablefiles(generator, directory, liststarts=False, unionoffsets=False, flushsize=8192, lendigits=16):
    if not isinstance(generator, oamap.generator.Generator):
        generator = generator.generator()
    if not os.path.exists(directory):
        os.mkdir(directory)
    fillables = {}
    _makefillables(generator, fillables, lambda name, dtype, dims: FillableFile(os.path.join(directory, name), dtype, dims=dims, flushsize=flushsize, lendigits=lendigits), liststarts, unionoffsets)
    return fillables

def fillablenumpyfiles(generator, directory, liststarts=False, unionoffsets=False, flushsize=8192, lendigits=16):
    if not isinstance(generator, oamap.generator.Generator):
        generator = generator.generator()
    if not os.path.exists(directory):
        os.mkdir(directory)
    fillables = {}
    _makefillables(generator, fillables, lambda name, dtype, dims: FillableNumpyFile(os.path.join(directory, name), dtype, dims=dims, flushsize=flushsize, lendigits=lendigits), liststarts, unionoffsets)
    return fillables
    
################################################################ FillableList

class FillableList(Fillable):
    def __init__(self, dtype, dims=()):
        self.dtype = dtype
        self.dims = dims
        self._data = []
        self._index = 0

    def append(self, value):
        # possibly correct for a previous exception (to ensure same semantics as FillableArray, FillableFile)
        if self._index < len(self._data):
            del self._data[self._index:]

        self._data.append(value)

        # no exceptions? acknowledge the new data point
        self._index += 1

    def extend(self, values):
        if self._index < len(self._data):
            del self._data[self._index:]

        self._data.extend(values)

        self._index += len(values)
        
    def __len__(self):
        return self._index

    def __getitem__(self, index):
        if isinstance(index, slice):
            lenself = len(self)
            start = 0       if index.start is None else index.start
            stop  = lenself if index.stop  is None else index.stop
            step  = 1       if index.step  is None else index.step
            if start < 0:
                start += lenself
            if stop < 0:
                stop += lenself
                
            start = min(lenself, max(0, start))
            stop  = min(lenself, max(0, stop))

            if step == 0:
                raise ValueError("slice step cannot be zero")
            else:
                length = (stop - start) // step
                out = numpy.empty((length,) + self.dims, dtype=self.dtype)
                out[:] = self._data[start:stop:step]
                return out

        else:
            return self._data[index]
        
################################################################ FillableArray

class FillableArray(Fillable):
    # Numpy arrays and list items have 96+8 byte (80+8 byte) overhead in Python 2 (Python 3)
    # compared to 8192 1-byte values (8-byte values), this is 1% overhead (0.1% overhead)
    def __init__(self, dtype, dims=(), chunksize=8192):
        self.dtype = dtype
        self.dims = dims
        self.chunksize = chunksize
        self._data = [numpy.empty((self.chunksize,) + self.dims, dtype=self.dtype)]
        self._indexinchunk = 0
        self._chunkindex = 0

    def append(self, value):
        # possibly add a new chunk
        if self._indexinchunk >= len(self._data[self._chunkindex]):
            while len(self._data) <= self._chunkindex + 1:
                self._data.append(numpy.empty((self.chunksize,) + self.dims, dtype=self.dtype))
            self._indexinchunk = 0
            self._chunkindex += 1

        self._data[self._chunkindex][self._indexinchunk] = value

        # no exceptions? acknowledge the new data point
        self._indexinchunk += 1

    def extend(self, values):
        chunkindex = self._chunkindex
        indexinchunk = self._indexinchunk

        while len(values) > 0:
            if indexinchunk >= len(self._data[chunkindex]):
                while len(self._data) <= chunkindex + 1:
                    self._data.append(numpy.empty((self.chunksize,) + self.dims, dtype=self.dtype))
                indexinchunk = 0
                chunkindex += 1

            tofill = min(len(values), self.chunksize - indexinchunk)
            self._data[chunkindex][indexinchunk : indexinchunk + tofill] = values[:tofill]
            indexinchunk += tofill
            values = values[tofill:]

        self._chunkindex = chunkindex
        self._indexinchunk = indexinchunk

    def __len__(self):
        return self._chunkindex*self.chunksize + self._indexinchunk

    def __getitem__(self, index):
        if isinstance(index, slice):
            lenself = len(self)
            start = 0       if index.start is None else index.start
            stop  = lenself if index.stop  is None else index.stop
            step  = 1       if index.step  is None else index.step
            if start < 0:
                start += lenself
            if stop < 0:
                stop += lenself

            start = min(lenself, max(0, start))
            stop  = min(lenself, max(0, stop))

            if step == 0:
                raise ValueError("slice step cannot be zero")

            else:
                length = (stop - start) // step
                out = numpy.empty((length,) + self.dims, dtype=self.dtype)
                outi = 0

                start_chunkindex, start_indexinchunk = divmod(start, self.chunksize)
                stop_chunkindex,  stop_indexinchunk  = divmod(stop,  self.chunksize)
                if step > 0:
                    stop_chunkindex += 1
                else:
                    stop_chunkindex -= 1

                offset = 0
                for chunkindex in xrange(start_chunkindex, stop_chunkindex, 1 if step > 0 else -1):
                    if step > 0:
                        if chunkindex == start_chunkindex:
                            begin = start_indexinchunk
                        else:
                            begin = offset
                        if chunkindex == stop_chunkindex - 1:
                            end = stop_indexinchunk
                        else:
                            end = self.chunksize

                    else:
                        if chunkindex == start_chunkindex:
                            begin = start_indexinchunk
                        else:
                            begin = self.chunksize - offset
                        if chunkindex == stop_chunkindex + 1:
                            end = stop_indexinchunk
                        else:
                            end = 0

                    array = self._data[chunkindex][begin:end:step]

                    offset = (end - begin) % step
                    out[outi : outi + len(array)] = array
                    outi += len(array)
                    if outi >= len(out):
                        break

                return out

        else:
            lenself = len(self)
            normalindex = index if index >= 0 else index + lenself
            if not 0 <= normalindex < lenself:
                raise IndexError("index {0} is out of bounds for size {1}".format(index, lenself))

            chunkindex, indexinchunk = divmod(index, self.chunksize)
            return self._data[chunkindex][indexinchunk]

################################################################ FillableFile

class FillableFile(Fillable):
    def __init__(self, filename, dtype, dims=(), flushsize=8192, lendigits=16):
        if not isinstance(dtype, numpy.dtype):
            dtype = numpy.dtype(dtype)
        self._data = numpy.empty((flushsize,) + dims, dtype=dtype)
        self._index = 0
        self._indexinchunk = 0
        self._indexflushed = 0
        self._filename = filename

        self._openfile(lendigits)

    def _openfile(self, lendigits):
        open(self._filename, "wb", 0).close()
        self._file = open(self._filename, "r+b", 0)
        self._datapos = 0
        # a plain file has no header

    @property
    def dtype(self):
        return self._data.dtype

    @property
    def dims(self):
        return self._data.shape[1:]

    def append(self, value):
        self._data[self._indexinchunk] = value

        # no exceptions? acknowledge the new data point
        self._index += 1
        self._indexinchunk += 1

        # possibly flush to file
        if self._indexinchunk >= len(self._data):
            self.flush()

    def extend(self, values):
        # extend flushes as much as it has to during write
        index = self._index
        indexinchunk = self._indexinchunk
        indexflushed = self._indexflushed

        while len(values) > 0:
            tofill = min(len(values), len(self._data) - indexinchunk)
            self._data[indexinchunk : indexinchunk + tofill] = values[:tofill]
            index += tofill
            indexinchunk += tofill
            values = values[tofill:]

            if len(values) > 0:
                self._file.seek(self._datapos + indexflushed*self.dtype.itemsize)
                self._file.write(self._data[:indexinchunk].tostring())
                indexinchunk = 0
                indexflushed = index
            
        self._index = index
        self._indexinchunk = indexinchunk
        self._indexflushed = indexflushed

    def flush(self):
        self._file.write(self._data[:self._indexinchunk].tostring())
        self._indexinchunk = 0
        self._indexflushed = self._index

    def __len__(self):
        return self._index

    def __getitem__(self, value):
        if not self._file.closed:
            self.flush()

        if isinstance(value, slice):
            array = numpy.memmap(self._filename, self.dtype, "r", self._datapos, (len(self),) + self.dims, "C")
            if value.start is None and value.stop is None and value.step is None:
                return array
            else:
                return array[value]

        else:
            lenself = len(self)
            normalindex = index if index >= 0 else index + lenself
            if not 0 <= normalindex < lenself:
                raise IndexError("index {0} is out of bounds for size {1}".format(index, lenself))

            if not self._file.closed:
                # since the file's still open, get it from here instead of making a new filehandle
                itemsize = self.dtype.itemsize
                try:
                    self._file.seek(self._datapos + normalindex*itemsize)
                    return numpy.fromstring(self._file.read(itemsize), self.dtype)[0]
                finally:
                    self._file.seek(self._datapos + self._indexflushed*self.dtype.itemsize)
            else:
                # otherwise, you have to open a new file
                with open(self._filename, "rb") as file:
                    file.seek(self._datapos + normalindex*itemsize)
                    return numpy.fromstring(file.read(itemsize), self.dtype)[0]

    def close(self):
        if hasattr(self, "_file"):
            self.flush()
            self._file.close()

    def __del__(self):
        self.close()

    def __enter__(self, *args, **kwds):
        return self

    def __exit__(self, *args, **kwds):
        self.close()

################################################################ FillableNumpyFile (FillableFile with a self-describing header)

class FillableNumpyFile(FillableFile):
    def _openfile(self, lendigits):
        magic = b"\x93NUMPY\x01\x00"
        header1 = "{{'descr': {0}, 'fortran_order': False, 'shape': (".format(repr(str(self.dtype))).encode("ascii")
        header2 = "{0}, }}".format(repr((10**lendigits - 1,) + self.dims)).encode("ascii")[1:]

        unpaddedlen = len(magic) + 2 + len(header1) + len(header2)
        paddedlen = int(math.ceil(float(unpaddedlen) / self.dtype.itemsize)) * self.dtype.itemsize
        header2 = header2 + b" " * (paddedlen - unpaddedlen)
        self._lenpos = len(magic) + 2 + len(header1)
        self._datapos = len(magic) + 2 + len(header1) + len(header2)
        assert self._datapos % self.dtype.itemsize == 0

        open(self._filename, "wb", 0).close()
        self._file = open(self._filename, "r+b", 0)
        self._formatter = "{0:%dd}" % lendigits
        self._file.write(magic)
        self._file.write(struct.pack("<H", len(header1) + len(header2)))
        self._file.write(header1)
        self._file.write(self._formatter.format(self._index).encode("ascii"))
        self._file.write(header2[lendigits:])

    def flush(self):
        self._file.seek(self._datapos + self._indexflushed*self.dtype.itemsize)
        self._file.write(self._data[:self._indexinchunk].tostring())
        self._file.seek(self._lenpos)
        self._file.write(self._formatter.format(self._index).encode("ascii"))
        self._indexinchunk = 0
        self._indexflushed = self._index
