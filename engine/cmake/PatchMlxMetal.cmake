function(replace_metal_command path original replacement)
  file(READ "${path}" content)
  string(FIND "${content}" "${original}" original_offset)
  if(original_offset EQUAL -1)
    string(FIND "${content}" "${replacement}" replacement_offset)
    if(replacement_offset EQUAL -1)
      message(FATAL_ERROR "MLX Metal command changed in ${path}; review the adapter")
    endif()
    return()
  endif()
  string(REPLACE "${original}" "${replacement}" content "${content}")
  file(WRITE "${path}" "${content}")
endfunction()

# The version probe runs inside a shell; kernel commands are CMake argument lists.
replace_metal_command("${MLX_SOURCE_DIR}/CMakeLists.txt"
  "xcrun -sdk macosx metal" "'\${GHOSTBAND_METAL_COMPILER}'")
set(kernels "${MLX_SOURCE_DIR}/mlx/backend/metal/kernels/CMakeLists.txt")
replace_metal_command("${kernels}"
  "xcrun -sdk macosx metal " "\"\${GHOSTBAND_METAL_COMPILER}\" ")
replace_metal_command("${kernels}"
  "xcrun -sdk macosx metallib " "\"\${GHOSTBAND_METAL_LINKER}\" ")

set(metal "${MLX_SOURCE_DIR}/mlx/backend/metal")
replace_metal_command("${metal}/CMakeLists.txt"
  "\${CMAKE_C_COMPILER}" "\"\${GHOSTBAND_METAL_COMPILER}\"")
replace_metal_command("${metal}/make_compiled_preamble.sh"
  [=[CCC="xcrun -sdk macosx metal -x metal"]=]
  [=[CCC=("$2" -x metal)]=])
replace_metal_command("${metal}/make_compiled_preamble.sh"
  [=[$( $CCC ]=] [=[$( "${CCC[@]}" ]=])
# Upstream ignores a failed expansion and embeds the compiler's error text as the JIT
# preamble, so the engine builds but every JIT kernel fails when the model first runs.
replace_metal_command("${metal}/make_compiled_preamble.sh"
  [=[# Remove any included system frameworks]=]
  [=[[ $? -eq 0 ] || { echo "$HDRS" >&2; exit 1; }
# Remove included system frameworks]=])
