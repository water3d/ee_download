import csv
import os
from pathlib import Path
from typing import Dict, Iterable, Union

import fiona
import rasterstats


def _get_fiona_args(polygon_path: Union[str, Path]) -> Dict[str, Union[str, Path]]:
	"""
	A simple utility that detects if, maybe, we're dealing with an Esri File Geodatabase. This is the wrong way
	to do this, but it'll work in many situations.

	Args:
		polygon_path (Union[str, Path]): File location of polygons.

	Returns:
		Dict[str, Union[str, Path]]: Returns the full path and, depending on the file format, the file name in a dictionary.
	"""

	parts = os.path.split(polygon_path)
	# If the folder name ends with .gdb and the "filename" doesn't have an extension, assume it's an FGDB
	if (parts[0].endswith(".gdb") or parts[0].endswith(".gpkg")) and "." not in parts[1]:
		return {'fp': parts[0], 'layer': parts[1]}
	else:
		return {'fp': polygon_path}


def zonal_stats(features: Union[str, Path],
				raster: Union[str, Path, None],
				output_folder: Union[str, Path, None],
				filename: str,
				keep_fields: Iterable[str] = ("UniqueID", "CLASS2"),
				stats: Iterable[str] = ('min', 'max', 'mean', 'median', 'std', 'count', 'percentile_10', 'percentile_90'),
				report_threshold: int = 1000,
				write_batch_size: int = 2000,
				use_points: bool = False,
				**kwargs) -> Union[str, Path, None]:
	# TODO: Make this check if raster and polys are in the same CRS - if they're not, then rasterstats doesn't
	#  automatically align them and we just get bad output.

	"""
	If the raster and the polygons are not in the CRS, this function will produce bad output.

	Args:
		features (Union[str, Path]): Location to the features.
		raster (Union[str, Path, None]): Location of the raster.
		output_folder ([Union[str, Path, None]): Output destination.
		filename (str): Name of the file.
		keep_fields (Iterable[str]): Fields that will be used.
		stats (Iterable[str]): The various statistical measurements to be computed.
		report_threshold (int): The number of iterations before it prints out the feature number it's on. Default is 1000. Set to None to disable.
		write_batch_size (int): The number of zones that should be stored up before writing to disk.
		use_points (bool): Switch rasterstats to extract using gen_point_query instead of gen_zonal_stats. See rasterstats
			package documentation for complete information. Get_point_query will get the values of a raster at all vertex
			locations when provided with a polygon or line. If provided points, it will extract those point values. We set
			interpolation to the nearest to perform an exact extraction of the cell values. In this codebase's usage, it's
			assumed that the "features" parameter to this function will be a points dataset (still in the same CRS as the raster)
			when use_points is True. Additionally, when this is True, the `stats` argument to this function is ignored
			as only a single value will be extracted as the attribute `value` in the output CSV. Default is False.
		kwargs: Passed through to rasterstats.

	Returns:
		Union[str, Path, None]: Zonal stats
	"""
	# Note the use of gen_zonal_stats, which uses a generator. That should mean that until we coerce it to a list on the
	# next line, each item isn't evaluated, which should prevent us from needing to store a geojson representation of
	# all the polygons at one time since we'll strip it off (it'd be bad to try to keep all of it

	# A silly hack to get fiona to open GDB data by splitting it only if the input is a gdb data item, then providing
	# anything else as kwargs. But fiona requires the main item to be an arg, not a kwarg
	kwargs = _get_fiona_args(features)
	main_file_path = kwargs['fp']
	del kwargs['fp']

	with fiona.open(main_file_path, **kwargs) as feats_open:

		if not use_points:  # If we want to do zonal, open a zonal stats generator
			zstats_results_geo = rasterstats.gen_zonal_stats(feats_open, raster, stats=stats, geojson_out=True, nodata=-9999, **kwargs)
			fieldnames = (*stats, *keep_fields)
			file_suffix = "zonal_stats"
		else:  # Otherwise, open a point query generator.
			# TODO: Need to make it convert the polygons to points here, otherwise it'll get the vertex data
			zstats_results_geo = rasterstats.gen_point_query(feats_open,
																raster,
																geojson_out=True,  # Need this to get extra attributes back
																nodata=-9999,
																interpolate="nearest",  # Need this or else rasterstats uses a mix of nearby cells, even for single points
																**kwargs)
			fieldnames = ("value", *keep_fields,)  # When doing point queries, we get a field called "value" back with the raster value
			file_suffix = "point_query"

		# Here's a first approach that still stores a lot in memory - it's commented out because we're instead
		# going to just generate them one by one and write them to a file directly.
		#
		# This next line is doing a lot of work. It's a dictionary comprehension inside a list comprehension -
		# we're going through each item in the results, then accessing just the properties key and constructing a new
		# dictionary just for the keys we want to keep - the keep fields (the key and a class field by defaiult) and
		# the stats fields zstats_results = [{key: poly['properties'][key] for key in fieldnames} for poly in
		# zstats_results_geo]

		i = 0
		output_filepath = os.path.join(str(output_folder), f"{filename}_{file_suffix}.csv")
		with open(output_filepath, 'w', newline='') as csv_file:
			writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
			writer.writeheader()
			results = []
			for poly in zstats_results_geo:  # Get the result for the polygon, then filter the keys with the dictionary comprehension below
				result = {key: poly['properties'][key] for key in fieldnames}

				for key in result:  # truncate the floats
					if type(result[key]) is float:
						result[key] = f"{result[key]:.5f}"

				i += 1
				results.append(result)
				if i % write_batch_size == 0:
					writer.writerows(results)  # Then write the lone result out one at a time to not store it all in RAM
					results = []

				if report_threshold and i % report_threshold == 0:
					print(i)

			if results:  # Clear out any remaining items at the end
				writer.writerows(results)
				print(i)

	return output_filepath
